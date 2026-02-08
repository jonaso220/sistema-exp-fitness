from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_wtf import CSRFProtect
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from urllib.parse import urlparse
import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth
import json
import logging
import math
import os

load_dotenv()

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-here')

# CSRF protection
csrf = CSRFProtect(app)

# Firebase initialization
try:
    firebase_creds_json = os.getenv('FIREBASE_CREDENTIALS')
    if firebase_creds_json:
        cred = credentials.Certificate(json.loads(firebase_creds_json))
    else:
        cred = credentials.Certificate('serviceAccountKey.json')
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    logger.info('Firebase initialized successfully')
except Exception as e:
    logger.error(f'Firebase initialization failed: {e}')
    raise

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
        try:
            doc = db.collection('users').document(user_id).get()
            if doc.exists:
                return User.from_dict(doc.id, doc.to_dict())
        except Exception as e:
            logger.error(f'Error getting user by id {user_id}: {e}')
        return None

    @staticmethod
    def get_by_field(field, value):
        try:
            docs = db.collection('users').where(field, '==', value).limit(1).get()
            for doc in docs:
                return User.from_dict(doc.id, doc.to_dict())
        except Exception as e:
            logger.error(f'Error getting user by {field}: {e}')
        return None

    def save(self):
        try:
            db.collection('users').document(self.id).set(self.to_dict())
        except Exception as e:
            logger.error(f'Error saving user {self.id}: {e}')
            raise

    def update_fields(self, **kwargs):
        try:
            for key, value in kwargs.items():
                setattr(self, key, value)
            db.collection('users').document(self.id).update(kwargs)
        except Exception as e:
            logger.error(f'Error updating user {self.id}: {e}')
            raise


# --- Validation helpers ---

def validate_positive_int(value, field_name, min_val=1, max_val=1440):
    """Validate and return a positive integer, or None on failure."""
    try:
        val = int(value)
        if val < min_val or val > max_val:
            return None
        return val
    except (TypeError, ValueError):
        return None


def validate_positive_float(value, field_name, min_val=0.1, max_val=500):
    """Validate and return a positive float, or None on failure."""
    try:
        val = float(value)
        if val < min_val or val > max_val:
            return None
        return val
    except (TypeError, ValueError):
        return None


def validate_url(url):
    """Validate that a URL is safe (http/https only)."""
    if not url:
        return ''
    try:
        parsed = urlparse(url)
        if parsed.scheme in ('http', 'https') and parsed.netloc:
            return url
    except Exception:
        pass
    return ''


# --- Helper functions ---

def calculate_exp_for_next_level(current_level):
    return math.floor(100 * (current_level ** 1.5))


def calculate_exp_gain(duration, intensity, has_evidence):
    multipliers = {'low': 1, 'medium': 1.5, 'high': 2}
    base_exp = duration * multipliers.get(intensity, 1)
    if has_evidence:
        base_exp *= 1.2
    return base_exp


def _get_activity_date(activity_dict):
    """Safely extract a date from an activity dict."""
    d = activity_dict.get('date')
    if d is None:
        return None
    if hasattr(d, 'date'):
        return d.date()
    if isinstance(d, str):
        try:
            return datetime.fromisoformat(d).date()
        except (ValueError, TypeError):
            return None
    return d


def calculate_streak(user_id):
    """Calculate consecutive days with activity."""
    today = datetime.utcnow().date()
    try:
        docs = (
            db.collection('activities')
            .where('user_id', '==', user_id)
            .get()
        )
    except Exception as e:
        logger.error(f'Error calculating streak for {user_id}: {e}')
        return 0

    activity_dates = set()
    for doc in docs:
        d = _get_activity_date(doc.to_dict())
        if d:
            activity_dates.add(d)

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
        {'name': 'Dedicacion', 'desc': 'Registra 10 actividades', 'icon': 'fa-medal', 'condition': total_activities >= 10},
        {'name': 'Consistente', 'desc': 'Registra 50 actividades', 'icon': 'fa-trophy', 'condition': total_activities >= 50},
        {'name': 'Maratonista', 'desc': 'Registra 100 actividades', 'icon': 'fa-running', 'condition': total_activities >= 100},
        {'name': 'Semana Perfecta', 'desc': 'Racha de 7 dias', 'icon': 'fa-fire', 'condition': streak >= 7},
        {'name': 'Mes Imparable', 'desc': 'Racha de 30 dias', 'icon': 'fa-fire-alt', 'condition': streak >= 30},
        {'name': 'Guerrero', 'desc': 'Alcanza nivel 5', 'icon': 'fa-shield-alt', 'condition': user.level >= 5},
        {'name': 'Campeon', 'desc': 'Alcanza nivel 10', 'icon': 'fa-crown', 'condition': user.level >= 10},
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

    try:
        docs = (
            db.collection('activities')
            .where('user_id', '==', user.id)
            .get()
        )
    except Exception as e:
        logger.error(f'Error checking inactivity for {user.id}: {e}')
        return

    last_date = None
    for doc in docs:
        d = _get_activity_date(doc.to_dict())
        if d and (last_date is None or d > last_date):
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
            flash(f'Penalizacion por {inactive_days} dia(s) de inactividad: -{penalty:.1f} EXP', 'warning')
            return

    user.update_fields(last_penalty_date=today_str)


def get_all_activities(user_id):
    """Get all activities for a user, ordered by date desc."""
    try:
        docs = (
            db.collection('activities')
            .where('user_id', '==', user_id)
            .get()
        )
    except Exception as e:
        logger.error(f'Error getting activities for {user_id}: {e}')
        return []

    activities = []
    for doc in docs:
        a = doc.to_dict()
        a['id'] = doc.id
        activities.append(a)

    def sort_key(x):
        d = x.get('date')
        if d is None:
            return datetime.min
        if isinstance(d, datetime):
            return d
        if hasattr(d, 'year'):
            return datetime(d.year, d.month, d.day)
        return datetime.min

    activities.sort(key=sort_key, reverse=True)
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
    try:
        apply_inactivity_penalty(current_user)
    except Exception as e:
        logger.error(f'Inactivity penalty error: {e}')

    all_activities = get_all_activities(current_user.id)
    recent_activities = all_activities[:10]
    total_activities = len(all_activities)

    exp_for_next = calculate_exp_for_next_level(current_user.level)
    progress = (current_user.exp / exp_for_next) * 100 if exp_for_next > 0 else 0

    streak = calculate_streak(current_user.id)
    achievements = get_achievements(current_user, total_activities, streak)
    unlocked_count = sum(1 for a in achievements if a['unlocked'])

    # Chart data: weight history
    weight_dates = []
    weight_values = []
    for a in reversed(all_activities):
        if a.get('weight_recorded'):
            d = a.get('date')
            date_str = d.strftime('%d/%m') if hasattr(d, 'strftime') else str(d)[:10]
            weight_dates.append(date_str)
            weight_values.append(a['weight_recorded'])

    # Chart data: activity breakdown by type
    type_counts = {}
    total_minutes = 0
    for a in all_activities:
        t = a.get('exercise_type', 'Otro')
        type_counts[t] = type_counts.get(t, 0) + 1
        total_minutes += a.get('duration', 0)

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
        exercise_type = request.form.get('exercise_type', '').strip()
        intensity = request.form.get('intensity', 'medium')

        valid_types = ['Cardio', 'Pesas', 'Yoga', 'Natacion', 'Ciclismo', 'Calistenia', 'HIIT', 'Otro']
        valid_intensities = ['low', 'medium', 'high']

        if exercise_type not in valid_types:
            flash('Tipo de ejercicio no valido.', 'danger')
            return redirect(url_for('add_activity'))

        if intensity not in valid_intensities:
            flash('Intensidad no valida.', 'danger')
            return redirect(url_for('add_activity'))

        duration = validate_positive_int(request.form.get('duration'), 'duration', 1, 1440)
        if duration is None:
            flash('Duracion debe ser entre 1 y 1440 minutos.', 'danger')
            return redirect(url_for('add_activity'))

        weight = validate_positive_float(request.form.get('weight'), 'weight', 10, 500)
        if weight is None:
            flash('Peso debe ser entre 10 y 500 kg.', 'danger')
            return redirect(url_for('add_activity'))

        has_evidence = 'evidence' in request.form
        evidence_url = validate_url(request.form.get('evidence_url', ''))

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

        try:
            db.collection('activities').add(activity_data)
        except Exception as e:
            logger.error(f'Error saving activity: {e}')
            flash('Error al guardar la actividad. Intenta de nuevo.', 'danger')
            return redirect(url_for('add_activity'))

        current_user.exp += exp_gained
        current_user.weight = weight

        while current_user.exp >= calculate_exp_for_next_level(current_user.level):
            current_user.exp -= calculate_exp_for_next_level(current_user.level)
            current_user.level += 1
            flash(f'Felicitaciones! Has subido al nivel {current_user.level}!', 'success')

        try:
            current_user.update_fields(
                exp=current_user.exp,
                weight=current_user.weight,
                level=current_user.level,
            )
        except Exception as e:
            logger.error(f'Error updating user after activity: {e}')

        flash(f'Actividad registrada: +{exp_gained:.1f} EXP', 'success')
        return redirect(url_for('dashboard'))

    return render_template('add_activity.html', user_weight=current_user.weight)


@app.route('/edit_activity/<activity_id>', methods=['GET', 'POST'])
@login_required
def edit_activity(activity_id):
    try:
        doc = db.collection('activities').document(activity_id).get()
        if not doc.exists:
            flash('Actividad no encontrada.', 'danger')
            return redirect(url_for('history'))
        activity = doc.to_dict()
        activity['id'] = doc.id
        if activity.get('user_id') != current_user.id:
            flash('No tienes permiso para editar esta actividad.', 'danger')
            return redirect(url_for('history'))
    except Exception as e:
        logger.error(f'Error fetching activity {activity_id}: {e}')
        flash('Error al cargar la actividad.', 'danger')
        return redirect(url_for('history'))

    if request.method == 'POST':
        exercise_type = request.form.get('exercise_type', '').strip()
        intensity = request.form.get('intensity', 'medium')

        valid_types = ['Cardio', 'Pesas', 'Yoga', 'Natacion', 'Ciclismo', 'Calistenia', 'HIIT', 'Otro']
        valid_intensities = ['low', 'medium', 'high']

        if exercise_type not in valid_types:
            flash('Tipo de ejercicio no valido.', 'danger')
            return redirect(url_for('edit_activity', activity_id=activity_id))

        if intensity not in valid_intensities:
            flash('Intensidad no valida.', 'danger')
            return redirect(url_for('edit_activity', activity_id=activity_id))

        duration = validate_positive_int(request.form.get('duration'), 'duration', 1, 1440)
        if duration is None:
            flash('Duracion debe ser entre 1 y 1440 minutos.', 'danger')
            return redirect(url_for('edit_activity', activity_id=activity_id))

        weight = validate_positive_float(request.form.get('weight'), 'weight', 10, 500)
        if weight is None:
            flash('Peso debe ser entre 10 y 500 kg.', 'danger')
            return redirect(url_for('edit_activity', activity_id=activity_id))

        has_evidence = 'evidence' in request.form
        evidence_url = validate_url(request.form.get('evidence_url', ''))

        new_exp = calculate_exp_gain(duration, intensity, has_evidence)
        old_exp = activity.get('exp_gained', 0)
        exp_diff = new_exp - old_exp

        try:
            db.collection('activities').document(activity_id).update({
                'exercise_type': exercise_type,
                'duration': duration,
                'intensity': intensity,
                'has_evidence': has_evidence,
                'evidence_url': evidence_url,
                'weight_recorded': weight,
                'exp_gained': new_exp,
            })
            current_user.exp = max(0, current_user.exp + exp_diff)
            current_user.update_fields(exp=current_user.exp, weight=weight)
            flash('Actividad actualizada.', 'success')
        except Exception as e:
            logger.error(f'Error updating activity {activity_id}: {e}')
            flash('Error al actualizar la actividad.', 'danger')

        return redirect(url_for('history'))

    return render_template('edit_activity.html', activity=activity)


@app.route('/delete_activity/<activity_id>', methods=['POST'])
@login_required
def delete_activity(activity_id):
    try:
        doc = db.collection('activities').document(activity_id).get()
        if not doc.exists:
            flash('Actividad no encontrada.', 'danger')
            return redirect(url_for('history'))
        activity = doc.to_dict()
        if activity.get('user_id') != current_user.id:
            flash('No tienes permiso para eliminar esta actividad.', 'danger')
            return redirect(url_for('history'))

        exp_lost = activity.get('exp_gained', 0)
        db.collection('activities').document(activity_id).delete()

        current_user.exp = max(0, current_user.exp - exp_lost)
        current_user.update_fields(exp=current_user.exp)

        flash(f'Actividad eliminada. -{exp_lost:.1f} EXP', 'info')
    except Exception as e:
        logger.error(f'Error deleting activity {activity_id}: {e}')
        flash('Error al eliminar la actividad.', 'danger')

    return redirect(url_for('history'))


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
            d = a.get('date')
            date_str = d.strftime('%d/%m') if hasattr(d, 'strftime') else str(d)[:10]
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
    page = max(1, min(page, total_pages))
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
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        weight_raw = request.form.get('weight')

        if not username or len(username) < 3 or len(username) > 30:
            flash('El nombre de usuario debe tener entre 3 y 30 caracteres.', 'danger')
            return redirect(url_for('register'))

        if not password or len(password) < 6:
            flash('La contrasena debe tener al menos 6 caracteres.', 'danger')
            return redirect(url_for('register'))

        weight = validate_positive_float(weight_raw, 'weight', 10, 500)
        if weight is None:
            flash('Peso debe ser entre 10 y 500 kg.', 'danger')
            return redirect(url_for('register'))

        if User.get_by_field('username', username):
            flash('El nombre de usuario ya existe.', 'danger')
            return redirect(url_for('register'))

        try:
            hashed_password = generate_password_hash(password)
            doc_ref = db.collection('users').document()
            user = User(
                id=doc_ref.id,
                username=username,
                password=hashed_password,
                weight=weight,
            )
            user.save()
            flash('Registro exitoso! Por favor inicia sesion.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            logger.error(f'Error registering user: {e}')
            flash('Error al registrar. Intenta de nuevo.', 'danger')
            return redirect(url_for('register'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user = User.get_by_field('username', username)

        if user and user.password and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Usuario o contrasena incorrectos.', 'danger')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/auth/google', methods=['POST'])
@csrf.exempt
def auth_google():
    id_token = request.form.get('id_token')
    if not id_token:
        flash('Error al iniciar sesion con Google.', 'danger')
        return redirect(url_for('login'))

    try:
        decoded_token = firebase_auth.verify_id_token(id_token)
        uid = decoded_token['uid']
        email = decoded_token.get('email')

        user = User.get_by_field('google_id', uid)

        if not user:
            if email and User.get_by_field('email', email):
                flash('El email ya esta registrado con otra cuenta.', 'danger')
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
            flash('Cuenta creada! Por favor ingresa tu peso inicial.', 'success')
            return redirect(url_for('complete_profile'))

        login_user(user)
        return redirect(url_for('dashboard'))

    except firebase_admin.exceptions.FirebaseError as e:
        logger.error(f'Firebase auth error: {e}')
        flash('Error al verificar la sesion de Google.', 'danger')
        return redirect(url_for('login'))
    except Exception as e:
        logger.error(f'Unexpected auth error: {e}')
        flash('Error al iniciar sesion.', 'danger')
        return redirect(url_for('login'))


@app.route('/complete-profile', methods=['GET', 'POST'])
@login_required
def complete_profile():
    if request.method == 'POST':
        weight = validate_positive_float(request.form.get('weight'), 'weight', 10, 500)
        if weight is None:
            flash('Peso debe ser entre 10 y 500 kg.', 'danger')
            return redirect(url_for('complete_profile'))
        try:
            current_user.update_fields(weight=weight)
            flash('Perfil completado!', 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            logger.error(f'Error completing profile: {e}')
            flash('Error al guardar. Intenta de nuevo.', 'danger')
    return render_template('complete_profile.html')


@app.errorhandler(404)
def not_found(e):
    return render_template('index.html'), 404


@app.errorhandler(500)
def server_error(e):
    logger.error(f'Server error: {e}')
    flash('Error interno del servidor. Intenta de nuevo.', 'danger')
    return redirect(url_for('index'))


@login_manager.user_loader
def load_user(user_id):
    return User.get_by_id(user_id)


if __name__ == '__main__':
    app.run(debug=True)
