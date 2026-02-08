from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_dance.contrib.google import make_google_blueprint, google
from flask_dance.consumer import oauth_authorized
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore
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
    def __init__(self, id, username, password=None, email=None, google_id=None, level=1, exp=0, weight=None):
        self.id = id
        self.username = username
        self.password = password
        self.email = email
        self.google_id = google_id
        self.level = level
        self.exp = float(exp) if exp else 0
        self.weight = float(weight) if weight else None

    def to_dict(self):
        data = {
            'username': self.username,
            'email': self.email,
            'google_id': self.google_id,
            'level': self.level,
            'exp': self.exp,
            'weight': self.weight,
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


# Google OAuth
google_bp = make_google_blueprint(
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    scope=['profile', 'email'],
)
app.register_blueprint(google_bp, url_prefix='/login')


def calculate_exp_for_next_level(current_level):
    return math.floor(100 * (current_level ** 1.5))


def calculate_exp_gain(duration, intensity, has_evidence):
    base_exp = duration * {
        'low': 1,
        'medium': 1.5,
        'high': 2,
    }[intensity]
    if has_evidence:
        base_exp *= 1.2
    return base_exp


@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')


@app.route('/dashboard')
@login_required
def dashboard():
    activities_docs = (
        db.collection('activities')
        .where('user_id', '==', current_user.id)
        .order_by('date', direction=firestore.Query.DESCENDING)
        .limit(10)
        .get()
    )

    activities = []
    for doc in activities_docs:
        activity = doc.to_dict()
        activity['id'] = doc.id
        activities.append(activity)

    exp_for_next = calculate_exp_for_next_level(current_user.level)
    progress = (current_user.exp / exp_for_next) * 100

    # Calculate streak
    today = datetime.utcnow().date()
    streak = 0

    if activities:
        last_date = activities[0]['date']
        if hasattr(last_date, 'date'):
            last_date = last_date.date()

        if (today - last_date).days <= 1:
            all_docs = (
                db.collection('activities')
                .where('user_id', '==', current_user.id)
                .order_by('date', direction=firestore.Query.DESCENDING)
                .get()
            )
            activity_dates = set()
            for doc in all_docs:
                d = doc.to_dict()['date']
                if hasattr(d, 'date'):
                    activity_dates.add(d.date())
                else:
                    activity_dates.add(d)

            current_date = today if today in activity_dates else last_date
            while current_date in activity_dates:
                streak += 1
                current_date -= timedelta(days=1)

    return render_template(
        'dashboard.html',
        user=current_user,
        activities=activities,
        exp_for_next=exp_for_next,
        progress=progress,
        streak=streak,
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

        exp_gained = calculate_exp_gain(duration, intensity, has_evidence)

        # Weight impact
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
            'weight_recorded': weight,
        }

        db.collection('activities').add(activity_data)

        current_user.exp += exp_gained
        current_user.weight = weight

        # Level up check
        while current_user.exp >= calculate_exp_for_next_level(current_user.level):
            current_user.exp -= calculate_exp_for_next_level(current_user.level)
            current_user.level += 1
            flash(f'¡Felicitaciones! Has subido al nivel {current_user.level}!', 'success')

        current_user.update_fields(
            exp=current_user.exp,
            weight=current_user.weight,
            level=current_user.level,
        )

        flash('Actividad registrada exitosamente!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('add_activity.html')


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


@oauth_authorized.connect_via(google_bp)
def google_logged_in(blueprint, token):
    if not token:
        flash('Error al iniciar sesión con Google.', 'danger')
        return False

    resp = blueprint.session.get('/oauth2/v1/userinfo')
    if not resp.ok:
        flash('Error al obtener información del usuario de Google.', 'danger')
        return False

    google_info = resp.json()
    google_user_id = str(google_info['id'])

    user = User.get_by_field('google_id', google_user_id)

    if not user:
        email = google_info.get('email')
        if User.get_by_field('email', email):
            flash('El email ya está registrado con otra cuenta.', 'danger')
            return False

        username = email.split('@')[0]
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
            google_id=google_user_id,
        )
        user.save()
        login_user(user)
        flash('¡Cuenta creada exitosamente! Por favor ingresa tu peso inicial.', 'success')
        return redirect(url_for('complete_profile'))

    login_user(user)
    flash('Inicio de sesión con Google exitoso.', 'success')
    return False


@app.route('/complete-profile', methods=['GET', 'POST'])
@login_required
def complete_profile():
    if request.method == 'POST':
        weight = float(request.form.get('weight'))
        current_user.update_fields(weight=weight)
        flash('¡Perfil completado exitosamente!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('complete_profile.html')


@login_manager.user_loader
def load_user(user_id):
    return User.get_by_id(user_id)


if __name__ == '__main__':
    app.run(debug=True)
