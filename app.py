from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_dance.contrib.google import make_google_blueprint, google
from flask_dance.consumer.storage.sqla import OAuthConsumerMixin, SQLAlchemyStorage
from flask_dance.consumer import oauth_authorized
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from oauthlib.oauth2.rfc6749.errors import InvalidGrantError, TokenExpiredError
from dotenv import load_dotenv
import math
import os

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-here')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fitness.db'
app.config['GOOGLE_CLIENT_ID'] = os.getenv('GOOGLE_CLIENT_ID')
app.config['GOOGLE_CLIENT_SECRET'] = os.getenv('GOOGLE_CLIENT_SECRET')

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=True)  # Nullable para usuarios de Google
    email = db.Column(db.String(120), unique=True, nullable=True)
    google_id = db.Column(db.String(100), unique=True, nullable=True)
    level = db.Column(db.Integer, default=1)
    exp = db.Column(db.Float, default=0)
    weight = db.Column(db.Float)
    activities = db.relationship('Activity', backref='user', lazy=True)

class OAuth(OAuthConsumerMixin, db.Model):
    user_id = db.Column(db.Integer, db.ForeignKey(User.id))
    user = db.relationship(User)

# Configuración de Google OAuth
google_bp = make_google_blueprint(
    client_id=app.config['GOOGLE_CLIENT_ID'],
    client_secret=app.config['GOOGLE_CLIENT_SECRET'],
    scope=['profile', 'email'],
    storage=SQLAlchemyStorage(
        OAuth,
        db.session,
        user=current_user,
        user_required=False
    )
)
app.register_blueprint(google_bp, url_prefix='/login')

class Activity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    exercise_type = db.Column(db.String(100), nullable=False)
    duration = db.Column(db.Integer)  # in minutes
    intensity = db.Column(db.String(20))  # 'low', 'medium', 'high'
    exp_gained = db.Column(db.Float)
    has_evidence = db.Column(db.Boolean, default=False)
    weight_recorded = db.Column(db.Float)

def calculate_exp_for_next_level(current_level):
    return math.floor(100 * (current_level ** 1.5))

def calculate_exp_gain(duration, intensity, has_evidence):
    base_exp = duration * {
        'low': 1,
        'medium': 1.5,
        'high': 2
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
    activities = Activity.query.filter_by(user_id=current_user.id).order_by(Activity.date.desc()).limit(10).all()
    exp_for_next = calculate_exp_for_next_level(current_user.level)
    progress = (current_user.exp / exp_for_next) * 100
    
    # Calculate streak
    today = datetime.utcnow().date()
    streak = 0
    last_activity = Activity.query.filter_by(user_id=current_user.id).order_by(Activity.date.desc()).first()
    
    if last_activity and (today - last_activity.date.date()).days <= 1:
        streak = 1
        current_date = last_activity.date.date()
        while True:
            previous_day = current_date - timedelta(days=1)
            activity = Activity.query.filter(
                Activity.user_id == current_user.id,
                db.func.date(Activity.date) == previous_day
            ).first()
            if not activity:
                break
            streak += 1
            current_date = previous_day

    return render_template('dashboard.html', 
                         user=current_user, 
                         activities=activities,
                         exp_for_next=exp_for_next,
                         progress=progress,
                         streak=streak)

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
            if weight_diff > 0:  # Weight loss
                exp_gained *= 1.1
            elif weight_diff < 0:  # Weight gain
                exp_gained *= 0.9
        
        activity = Activity(
            user_id=current_user.id,
            exercise_type=exercise_type,
            duration=duration,
            intensity=intensity,
            exp_gained=exp_gained,
            has_evidence=has_evidence,
            weight_recorded=weight
        )
        
        current_user.exp += exp_gained
        current_user.weight = weight
        
        # Level up check
        while current_user.exp >= calculate_exp_for_next_level(current_user.level):
            current_user.exp -= calculate_exp_for_next_level(current_user.level)
            current_user.level += 1
            flash(f'¡Felicitaciones! Has subido al nivel {current_user.level}!', 'success')
        
        db.session.add(activity)
        db.session.commit()
        
        flash('Actividad registrada exitosamente!', 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('add_activity.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        weight = float(request.form.get('weight'))
        
        if User.query.filter_by(username=username).first():
            flash('El nombre de usuario ya existe', 'danger')
            return redirect(url_for('register'))
        
        hashed_password = generate_password_hash(password)
        new_user = User(username=username, password=hashed_password, weight=weight)
        
        db.session.add(new_user)
        db.session.commit()
        
        flash('¡Registro exitoso! Por favor inicia sesión', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password, password):
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

    # Buscar usuario existente
    user = User.query.filter_by(google_id=google_user_id).first()

    if not user:
        # Crear nuevo usuario
        email = google_info.get('email')
        if User.query.filter_by(email=email).first():
            flash('El email ya está registrado con otra cuenta.', 'danger')
            return False

        username = email.split('@')[0]
        base_username = username
        counter = 1
        while User.query.filter_by(username=username).first():
            username = f"{base_username}{counter}"
            counter += 1

        user = User(
            username=username,
            email=email,
            google_id=google_user_id
        )
        db.session.add(user)
        db.session.commit()
        flash('¡Cuenta creada exitosamente! Por favor ingresa tu peso inicial.', 'success')
        return redirect(url_for('complete_profile'))

    login_user(user)
    flash('Inicio de sesión con Google exitoso.', 'success')
    return False  # False para evitar que Flask-Dance haga el login automático

@app.route('/complete-profile', methods=['GET', 'POST'])
@login_required
def complete_profile():
    if request.method == 'POST':
        weight = float(request.form.get('weight'))
        current_user.weight = weight
        db.session.commit()
        flash('¡Perfil completado exitosamente!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('complete_profile.html')

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
