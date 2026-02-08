from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth
import json
import math
import os

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-here')

# Firebase initialization
firebase_creds_json = os.getenv('FIREBASE_CREDENTIALS')
if firebase_creds_json:
    cred = credentials.Certificate(json.loads(firebase_creds_json))
else:
    cred = credentials.Certificate('serviceAccountKey.json')
firebase_admin.initialize_app(cred)
db = firestore.client()

login_manager = LoginManager(app)
login_manager.login_view = 'login'


class User(UserMixin):
    def __init__(self, id, username, password=None, email=None, google_id=None,
                 level=1, exp=0, weight=None, last_penalty_date=None):
        self.id = id
        self.username = username
        self.password = password
        self.email = email
        self.google_id = google_id
        self.level = level
        self.exp = float(exp) if exp else 0
        self.weight = float(weight) if weight else None
        self.last_penalty_date = last_penalty_date

    def to_dict(self):
        data = {
            'username': self.username,
            'email': self.email,
            'google_id': self.google_id,
            'level': self.level,
            'exp': self.exp,
            'weight': self.weight,
            'last_penalty_date': self.last_penalty_date,
        }
        if self.password is not None:
            data['password'] = self.password
        return data

    @staticmethod
    def from_dict(doc_id, data):
        return User(
            id=doc_id,
            username=data.get('username'),
            password=data.get('password'),
            email=data.get('email'),
            google_id=data.get('google_id'),
            level=data.get('level', 1),
            exp=data.get('exp', 0),
            weight=data.get('weight'),
            last_penalty_date=data.get('last_penalty_date'),
        )

    @staticmethod
    def get_by_id(user_id):
        doc = db.collection('users').document(user_id).get()
        if doc.exists:
            return User.from_dict(doc.id, doc.to_dict())
        return None

    @staticmethod
    def get_by_field(field, value):
        docs = db.collection('users').where(field, '==', value).limit(1).get()
        for doc in docs:
            return User.from_dict(doc.id, doc.to_dict())
        return None

    def save(self):
        db.collection('users').document(self.id).set(self.to_dict())

    def update_fields(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        db.collection('users').document(self.id).update(kwargs)


# --- Helper functions ---

def calculate_exp_for_next_level(current_level):
    return math.floor(100 * (current_level ** 1.5))


def calculate_exp_gain(duration, intensity, has_evidence):
    base_exp = duration * {'low': 1, 'medium': 1.5, 'high': 2}[intensity]
    if has_evidence:
        base_exp *= 1.2
    return base_exp


def calculate_streak(user_id):
    """Calculate consecutive days with activity."""
    today = datetime.utcnow().date()
    docs = (
        db.collection('activities')
        .where('user_id', '==', user_id)
        .get()
    )
    activity_dates = set()
    for doc in docs:
        d = doc.to_dict()['date']
        activity_dates.add(d.date() if hasattr(d, 'date') else d)

    if not activity_dates:
        return 0

    last_date = max(activity_dates)
    if (today - last_date).days > 1:
        return 0

    streak = 0
    current_date = today if today in activity_dates else last_date
    while current_date in activity_dates:
        streak += 1
        current_date -= timedelta(days=1)
    return streak


def get_achievements(user, total_activities, streak):
    """Calculate which achievements are unlocked."""
    all_achievements = [
        {'name': 'Primer Paso', 'desc': 'Registra tu primera actividad', 'icon': 'fa-shoe-prints', 'condition': total_activities >= 1},
        {'name': 'Dedicación', 'desc': 'Registra 10 actividades', 'icon': 'fa-medal', 'condition': total_activities >= 10},
        {'name': 'Consistente', 'desc': 'Registra 50 actividades', 'icon': 'fa-trophy', 'condition': total_activities >= 50},
        {'name': 'Maratonista', 'desc': 'Registra 100 actividades', 'icon': 'fa-running', 'condition': total_activities >= 100},
        {'name': 'Semana Perfecta', 'desc': 'Racha de 7 días', 'icon': 'fa-fire', 'condition': streak >= 7},
        {'name': 'Mes Imparable', 'desc': 'Racha de 30 días', 'icon': 'fa-fire-alt', 'condition': streak >= 30},
        {'name': 'Guerrero', 'desc': 'Alcanza nivel 5', 'icon': 'fa-shield-alt', 'condition': user.level >= 5},
        {'name': 'Campeón', 'desc': 'Alcanza nivel 10', 'icon': 'fa-crown', 'condition': user.level >= 10},
        {'name': 'Leyenda', 'desc': 'Alcanza nivel 25', 'icon': 'fa-gem', 'condition': user.level >= 25},
    ]
    for a in all_achievements:
        a['unlocked'] = a.pop('condition')
    return all_achievements


def apply_inactivity_penalty(user):
    """Deduct 5% EXP per inactive day (max 7 days). Applied once per day."""
    today_str = datetime.utcnow().date().isoformat()
    if user.last_penalty_date == today_str:
        return

    docs = (
        db.collection('activities')
        .where('user_id', '==', user.id)
        .get()
    )
    last_date = None
    for doc in docs:
        d = doc.to_dict()['date']
        d = d.date() if hasattr(d, 'date') else d
        if last_date is None or d > last_date:
            last_date = d

    if not last_date:
        user.update_fields(last_penalty_date=today_str)
        return

    inactive_days = (datetime.utcnow().date() - last_date).days - 1
    if inactive_days > 0:
        penalty_days = min(inactive_days, 7)
        penalty = user.exp * 0.05 * penalty_days
        if penalty > 0:
            new_exp = max(0, user.exp - penalty)
            user.update_fields(exp=new_exp, last_penalty_date=today_str)
            user.exp = new_exp
            flash(f'Penalización por {inactive_days} día(s) de inactividad: -{penalty:.1f} EXP', 'warning')
            return

    user.update_fields(last_penalty_date=today_str)


def get_all_activities(user_id):
    """Get all activities for a user, ordered by date desc."""
    docs = (
        db.collection('activities')
        .where('user_id', '==', user_id)
        .get()
    )
    activities = []
    for doc in docs:
        a = doc.to_dict()
        a['id'] = doc.id
        activities.append(a)
    activities.sort(key=lambda x: x.get('date', datetime.min), reverse=True)
    return activities


# --- Routes ---

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')


@app.route('/dashboard')
@login_required
def dashboard():
    apply_inactivity_penalty(current_user)

    all_activities = get_all_activities(current_user.id)
    recent_activities = all_activities[:10]
    total_activities = len(all_activities)

    exp_for_next = calculate_exp_for_next_level(current_user.level)
    progress = (current_user.exp / exp_for_next) * 100

    streak = calculate_streak(current_user.id)
    achievements = get_achievements(current_user, total_activities, streak)
    unlocked_count = sum(1 for a in achievements if a['unlocked'])

    # Chart data: weight history
    weight_dates = []
    weight_values = []
    for a in reversed(all_activities):
        if a.get('weight_recorded'):
            d = a['date']
            date_str = d.strftime('%d/%m') if hasattr(d, 'strftime') else str(d)
            weight_dates.append(date_str)
            weight_values.append(a['weight_recorded'])

    # Chart data: activity breakdown by type
    type_counts = {}
    for a in all_activities:
        t = a.get('exercise_type', 'Otro')
        type_counts[t] = type_counts.get(t, 0) + 1

    total_minutes = sum(a.get('duration', 0) for a in all_activities)

    return render_template(
        'dashboard.html',
        user=current_user,
        activities=recent_activities,
        exp_for_next=exp_for_next,
        progress=progress,
        streak=streak,
        achievements=achievements,
        unlocked_count=unlocked_count,
        total_activities=total_activities,
        total_minutes=total_minutes,
        weight_dates=json.dumps(weight_dates[-20:]),
        weight_values=json.dumps(weight_values[-20:]),
        type_labels=json.dumps(list(type_counts.keys())),
        type_data=json.dumps(list(type_counts.values())),
    )


@app.route('/add_activity', methods=['GET', 'POST'])
@login_required
def add_activity():
    if request.method == 'POST':
        exercise_type = request.form.get('exercise_type')
        duration = int(request.form.get('duration'))
        intensity = request.form.get('intensity')
        has_evidence = 'evidence' in request.form
        weight = float(request.form.get('weight', 0))
        evidence_url = request.form.get('evidence_url', '')

        exp_gained = calculate_exp_gain(duration, intensity, has_evidence)

        if current_user.weight:
            weight_diff = current_user.weight - weight
            if weight_diff > 0:
                exp_gained *= 1.1
            elif weight_diff < 0:
                exp_gained *= 0.9

        activity_data = {
            'user_id': current_user.id,
            'date': datetime.utcnow(),
            'exercise_type': exercise_type,
            'duration': duration,
            'intensity': intensity,
            'exp_gained': exp_gained,
            'has_evidence': has_evidence,
            'evidence_url': evidence_url,
            'weight_recorded': weight,
        }

        db.collection('activities').add(activity_data)

        current_user.exp += exp_gained
        current_user.weight = weight

        while current_user.exp >= calculate_exp_for_next_level(current_user.level):
            current_user.exp -= calculate_exp_for_next_level(current_user.level)
            current_user.level += 1
            flash(f'¡Felicitaciones! Has subido al nivel {current_user.level}!', 'success')

        current_user.update_fields(
            exp=current_user.exp,
            weight=current_user.weight,
            level=current_user.level,
        )

        flash(f'Actividad registrada: +{exp_gained:.1f} EXP', 'success')
        return redirect(url_for('dashboard'))

    return render_template('add_activity.html')


@app.route('/profile')
@login_required
def profile():
    all_activities = get_all_activities(current_user.id)
    total_activities = len(all_activities)
    total_exp = sum(a.get('exp_gained', 0) for a in all_activities)
    total_minutes = sum(a.get('duration', 0) for a in all_activities)
    streak = calculate_streak(current_user.id)
    achievements = get_achievements(current_user, total_activities, streak)

    weight_dates = []
    weight_values = []
    for a in reversed(all_activities):
        if a.get('weight_recorded'):
            d = a['date']
            date_str = d.strftime('%d/%m') if hasattr(d, 'strftime') else str(d)
            weight_dates.append(date_str)
            weight_values.append(a['weight_recorded'])

    return render_template(
        'profile.html',
        user=current_user,
        total_activities=total_activities,
        total_exp=total_exp,
        total_minutes=total_minutes,
        streak=streak,
        achievements=achievements,
        weight_dates=json.dumps(weight_dates[-30:]),
        weight_values=json.dumps(weight_values[-30:]),
    )


@app.route('/history')
@login_required
def history():
    page = request.args.get('page', 1, type=int)
    per_page = 15
    all_activities = get_all_activities(current_user.id)
    total = len(all_activities)
    total_pages = max(1, math.ceil(total / per_page))
    start = (page - 1) * per_page
    activities = all_activities[start:start + per_page]

    return render_template(
        'history.html',
        activities=activities,
        page=page,
        total_pages=total_pages,
        total=total,
    )


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        weight = float(request.form.get('weight'))

        if User.get_by_field('username', username):
            flash('El nombre de usuario ya existe', 'danger')
            return redirect(url_for('register'))

        hashed_password = generate_password_hash(password)
        doc_ref = db.collection('users').document()
        user = User(
            id=doc_ref.id,
            username=username,
            password=hashed_password,
            weight=weight,
        )
        user.save()

        flash('¡Registro exitoso! Por favor inicia sesión', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = User.get_by_field('username', username)

        if user and user.password and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Usuario o contraseña incorrectos', 'danger')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/auth/google', methods=['POST'])
def auth_google():
    id_token = request.form.get('id_token')
    if not id_token:
        flash('Error al iniciar sesión con Google.', 'danger')
        return redirect(url_for('login'))

    try:
        decoded_token = firebase_auth.verify_id_token(id_token)
        uid = decoded_token['uid']
        email = decoded_token.get('email')

        user = User.get_by_field('google_id', uid)

        if not user:
            if email and User.get_by_field('email', email):
                flash('El email ya está registrado con otra cuenta.', 'danger')
                return redirect(url_for('login'))

            username = email.split('@')[0] if email else uid[:8]
            base_username = username
            counter = 1
            while User.get_by_field('username', username):
                username = f"{base_username}{counter}"
                counter += 1

            doc_ref = db.collection('users').document()
            user = User(
                id=doc_ref.id,
                username=username,
                email=email,
                google_id=uid,
            )
            user.save()
            login_user(user)
            flash('¡Cuenta creada! Por favor ingresa tu peso inicial.', 'success')
            return redirect(url_for('complete_profile'))

        login_user(user)
        return redirect(url_for('dashboard'))

    except Exception:
        flash('Error al verificar la sesión de Google.', 'danger')
        return redirect(url_for('login'))


@app.route('/complete-profile', methods=['GET', 'POST'])
@login_required
def complete_profile():
    if request.method == 'POST':
        weight = float(request.form.get('weight'))
        current_user.update_fields(weight=weight)
        flash('¡Perfil completado!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('complete_profile.html')


@login_manager.user_loader
def load_user(user_id):
    return User.get_by_id(user_id)


if __name__ == '__main__':
    app.run(debug=True)
