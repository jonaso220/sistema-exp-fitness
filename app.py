from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, make_response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_wtf import CSRFProtect
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from urllib.parse import urlparse
from collections import defaultdict
from functools import wraps
from translations import TRANSLATIONS
import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth
import json
import logging
import math
import os
import re
import time
import secrets

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

secret_key = os.getenv('SECRET_KEY')
if not secret_key:
    logger.warning('SECRET_KEY not set! Using random key.')
    secret_key = os.urandom(32).hex()
app.config['SECRET_KEY'] = secret_key
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=2)

csrf = CSRFProtect(app)


@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    if os.getenv('FLASK_ENV') == 'production':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


_rate_limit_store = defaultdict(list)


def _is_rate_limited(key, max_attempts=5, window_seconds=60):
    now = time.time()
    _rate_limit_store[key] = [t for t in _rate_limit_store[key] if now - t < window_seconds]
    if len(_rate_limit_store[key]) >= max_attempts:
        return True
    _rate_limit_store[key].append(now)
    return False


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

# =============================================================================
# CONSTANTS
# =============================================================================

VALID_EXERCISE_TYPES = [
    'Cardio', 'Pesas', 'Yoga', 'Natacion', 'Ciclismo', 'Calistenia', 'HIIT',
    'Futbol', 'Baloncesto', 'Tenis', 'Padel', 'Boxeo', 'Artes Marciales',
    'Crossfit', 'Escalada', 'Senderismo', 'Remo', 'Otro',
]
VALID_INTENSITIES = ['low', 'medium', 'high']
STRENGTH_EXERCISES = ['Pesas', 'Calistenia', 'Crossfit']
DISTANCE_EXERCISES = ['Cardio', 'Ciclismo', 'Natacion', 'Senderismo', 'Remo']

# =============================================================================
# PLAYER CLASSES
# =============================================================================

PLAYER_CLASSES = {
    'guerrero': {
        'name_es': 'Guerrero', 'name_en': 'Warrior', 'icon': 'fa-shield-alt',
        'color': '#ef4444', 'gradient': 'linear-gradient(135deg, #ef4444, #dc2626)',
        'desc_es': 'Maestro de las pesas y la fuerza bruta. Bonus en Pesas y Calistenia.',
        'desc_en': 'Master of weights and brute strength. Bonus in Weights and Calisthenics.',
        'specialty': ['Pesas', 'Calistenia'], 'bonus': 0.3,
    },
    'corredor': {
        'name_es': 'Corredor', 'name_en': 'Runner', 'icon': 'fa-running',
        'color': '#10b981', 'gradient': 'linear-gradient(135deg, #10b981, #059669)',
        'desc_es': 'Dominador del cardio y la resistencia. Bonus en Cardio y HIIT.',
        'desc_en': 'Master of cardio and endurance. Bonus in Cardio and HIIT.',
        'specialty': ['Cardio', 'HIIT'], 'bonus': 0.3,
    },
    'monje': {
        'name_es': 'Monje', 'name_en': 'Monk', 'icon': 'fa-pray',
        'color': '#06b6d4', 'gradient': 'linear-gradient(135deg, #06b6d4, #0891b2)',
        'desc_es': 'Maestro del equilibrio cuerpo-mente. Bonus en Yoga y Artes Marciales.',
        'desc_en': 'Master of mind-body balance. Bonus in Yoga and Martial Arts.',
        'specialty': ['Yoga', 'Artes Marciales'], 'bonus': 0.3,
    },
    'nadador': {
        'name_es': 'Nadador', 'name_en': 'Swimmer', 'icon': 'fa-swimmer',
        'color': '#3b82f6', 'gradient': 'linear-gradient(135deg, #3b82f6, #2563eb)',
        'desc_es': 'Conquistador del agua. Bonus en Natacion y Remo.',
        'desc_en': 'Conqueror of the water. Bonus in Swimming and Rowing.',
        'specialty': ['Natacion', 'Remo'], 'bonus': 0.3,
    },
    'ciclista': {
        'name_es': 'Ciclista', 'name_en': 'Cyclist', 'icon': 'fa-bicycle',
        'color': '#f59e0b', 'gradient': 'linear-gradient(135deg, #f59e0b, #d97706)',
        'desc_es': 'Rey de las dos ruedas y la montana. Bonus en Ciclismo y Senderismo.',
        'desc_en': 'King of two wheels and the mountain. Bonus in Cycling and Hiking.',
        'specialty': ['Ciclismo', 'Senderismo'], 'bonus': 0.3,
    },
    'deportista': {
        'name_es': 'Deportista', 'name_en': 'Athlete', 'icon': 'fa-futbol',
        'color': '#8b5cf6', 'gradient': 'linear-gradient(135deg, #8b5cf6, #7c3aed)',
        'desc_es': 'Atleta de equipo y competicion. Bonus en Futbol, Baloncesto, Tenis y Padel.',
        'desc_en': 'Team and competition athlete. Bonus in Football, Basketball, Tennis and Padel.',
        'specialty': ['Futbol', 'Baloncesto', 'Tenis', 'Padel'], 'bonus': 0.3,
    },
    'luchador': {
        'name_es': 'Luchador', 'name_en': 'Fighter', 'icon': 'fa-fist-raised',
        'color': '#ec4899', 'gradient': 'linear-gradient(135deg, #ec4899, #db2777)',
        'desc_es': 'Guerrero del ring y la calle. Bonus en Boxeo, Crossfit y Escalada.',
        'desc_en': 'Ring and street warrior. Bonus in Boxing, CrossFit and Climbing.',
        'specialty': ['Boxeo', 'Crossfit', 'Escalada'], 'bonus': 0.3,
    },
}

# =============================================================================
# CHALLENGE DEFINITIONS
# =============================================================================

WEEKLY_CHALLENGE_TEMPLATES = [
    {'key': 'w_3sessions', 'name_es': '3 sesiones esta semana', 'name_en': '3 sessions this week',
     'desc_es': 'Registra 3 actividades', 'desc_en': 'Log 3 activities',
     'target': 3, 'type': 'activity_count', 'reward_exp': 100, 'icon': 'fa-calendar-check'},
    {'key': 'w_150min', 'name_es': '150 minutos activo', 'name_en': '150 active minutes',
     'desc_es': 'Acumula 150 minutos de ejercicio', 'desc_en': 'Accumulate 150 minutes of exercise',
     'target': 150, 'type': 'total_minutes', 'reward_exp': 150, 'icon': 'fa-clock'},
    {'key': 'w_high_intensity', 'name_es': 'Sesion de alta intensidad', 'name_en': 'High intensity session',
     'desc_es': 'Completa una sesion de alta intensidad', 'desc_en': 'Complete a high intensity session',
     'target': 1, 'type': 'high_intensity_count', 'reward_exp': 75, 'icon': 'fa-bolt'},
    {'key': 'w_variety', 'name_es': 'Variedad de ejercicio', 'name_en': 'Exercise variety',
     'desc_es': 'Practica 3 tipos de ejercicio diferentes', 'desc_en': 'Practice 3 different exercise types',
     'target': 3, 'type': 'unique_types', 'reward_exp': 120, 'icon': 'fa-random'},
]

MONTHLY_CHALLENGE_TEMPLATES = [
    {'key': 'm_20sessions', 'name_es': '20 sesiones este mes', 'name_en': '20 sessions this month',
     'desc_es': 'Registra 20 actividades este mes', 'desc_en': 'Log 20 activities this month',
     'target': 20, 'type': 'activity_count', 'reward_exp': 300, 'icon': 'fa-trophy'},
    {'key': 'm_1000min', 'name_es': '1000 minutos activo', 'name_en': '1000 active minutes',
     'desc_es': 'Acumula 1000 minutos de ejercicio este mes', 'desc_en': 'Accumulate 1000 minutes this month',
     'target': 1000, 'type': 'total_minutes', 'reward_exp': 500, 'icon': 'fa-hourglass-half'},
    {'key': 'm_streak7', 'name_es': 'Racha de 7 dias', 'name_en': '7-day streak',
     'desc_es': 'Manten una racha de 7 dias consecutivos', 'desc_en': 'Maintain a 7-day streak',
     'target': 7, 'type': 'streak', 'reward_exp': 400, 'icon': 'fa-fire'},
    {'key': 'm_5000exp', 'name_es': '5000 EXP este mes', 'name_en': '5000 EXP this month',
     'desc_es': 'Gana 5000 EXP este mes', 'desc_en': 'Earn 5000 EXP this month',
     'target': 5000, 'type': 'total_exp', 'reward_exp': 350, 'icon': 'fa-star'},
]

# =============================================================================
# USER MODEL
# =============================================================================

class User(UserMixin):
    def __init__(self, id, username, password=None, email=None, google_id=None,
                 level=1, exp=0, weight=None, last_penalty_date=None,
                 player_class=None, class_selected_at=None,
                 language='es', theme='dark', claimed_challenges=None, api_token=None):
        self.id = id
        self.username = username
        self.password = password
        self.email = email
        self.google_id = google_id
        self.level = level
        self.exp = float(exp) if exp else 0
        self.weight = float(weight) if weight else None
        self.last_penalty_date = last_penalty_date
        self.player_class = player_class
        self.class_selected_at = class_selected_at
        self.language = language or 'es'
        self.theme = theme or 'dark'
        self.claimed_challenges = claimed_challenges or []
        self.api_token = api_token

    def to_dict(self):
        data = {
            'username': self.username, 'email': self.email, 'google_id': self.google_id,
            'level': self.level, 'exp': self.exp, 'weight': self.weight,
            'last_penalty_date': self.last_penalty_date, 'player_class': self.player_class,
            'class_selected_at': self.class_selected_at, 'language': self.language,
            'theme': self.theme, 'claimed_challenges': self.claimed_challenges,
            'api_token': self.api_token,
        }
        if self.password is not None:
            data['password'] = self.password
        return data

    @staticmethod
    def from_dict(doc_id, data):
        return User(
            id=doc_id, username=data.get('username'), password=data.get('password'),
            email=data.get('email'), google_id=data.get('google_id'),
            level=data.get('level', 1), exp=data.get('exp', 0), weight=data.get('weight'),
            last_penalty_date=data.get('last_penalty_date'), player_class=data.get('player_class'),
            class_selected_at=data.get('class_selected_at'), language=data.get('language', 'es'),
            theme=data.get('theme', 'dark'), claimed_challenges=data.get('claimed_challenges', []),
            api_token=data.get('api_token'),
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


# =============================================================================
# i18n CONTEXT PROCESSOR
# =============================================================================

@app.context_processor
def inject_i18n():
    lang = 'es'
    if current_user.is_authenticated:
        lang = getattr(current_user, 'language', 'es') or 'es'
    else:
        lang = request.cookies.get('lang', 'es')
    translations = TRANSLATIONS.get(lang, TRANSLATIONS['es'])
    def t(key):
        return translations.get(key, key)
    return {
        't': t, 'current_lang': lang,
        'current_theme': getattr(current_user, 'theme', 'dark') if current_user.is_authenticated else request.cookies.get('theme', 'dark'),
        'PLAYER_CLASSES': PLAYER_CLASSES,
    }


# =============================================================================
# VALIDATION HELPERS
# =============================================================================

def validate_positive_int(value, field_name, min_val=1, max_val=1440):
    try:
        val = int(value)
        return val if min_val <= val <= max_val else None
    except (TypeError, ValueError):
        return None

def validate_positive_float(value, field_name, min_val=0.1, max_val=500):
    try:
        val = float(value)
        return val if min_val <= val <= max_val else None
    except (TypeError, ValueError):
        return None

def validate_url(url):
    if not url:
        return ''
    url = url.strip()
    if len(url) > 2048:
        return ''
    try:
        parsed = urlparse(url)
        if parsed.scheme in ('http', 'https') and parsed.netloc:
            if re.match(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', parsed.netloc):
                return url
    except Exception:
        pass
    return ''

def validate_base64_image(data):
    if not data or not data.startswith('data:image/') or len(data) > 700000:
        return ''
    return data


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def calculate_exp_for_next_level(current_level):
    return math.floor(100 * (current_level ** 1.5))

def calculate_exp_gain(duration, intensity, has_evidence, user=None, exercise_type=None):
    multipliers = {'low': 1, 'medium': 1.5, 'high': 2}
    base_exp = duration * multipliers.get(intensity, 1)
    if has_evidence:
        base_exp *= 1.2
    if user and exercise_type and getattr(user, 'player_class', None):
        cls = PLAYER_CLASSES.get(user.player_class)
        if cls and exercise_type in cls.get('specialty', []):
            base_exp *= (1 + cls['bonus'])
    return base_exp

def _get_activity_date(activity_dict):
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
    today = datetime.utcnow().date()
    try:
        docs = db.collection('activities').where('user_id', '==', user_id).get()
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
    achievements = [
        {'name': 'Primer Paso', 'name_en': 'First Step', 'desc': 'Registra tu primera actividad', 'desc_en': 'Log your first activity', 'icon': 'fa-shoe-prints', 'condition': total_activities >= 1},
        {'name': 'Dedicacion', 'name_en': 'Dedication', 'desc': 'Registra 10 actividades', 'desc_en': 'Log 10 activities', 'icon': 'fa-medal', 'condition': total_activities >= 10},
        {'name': 'Consistente', 'name_en': 'Consistent', 'desc': 'Registra 50 actividades', 'desc_en': 'Log 50 activities', 'icon': 'fa-trophy', 'condition': total_activities >= 50},
        {'name': 'Maratonista', 'name_en': 'Marathoner', 'desc': 'Registra 100 actividades', 'desc_en': 'Log 100 activities', 'icon': 'fa-running', 'condition': total_activities >= 100},
        {'name': 'Semana Perfecta', 'name_en': 'Perfect Week', 'desc': 'Racha de 7 dias', 'desc_en': '7-day streak', 'icon': 'fa-fire', 'condition': streak >= 7},
        {'name': 'Mes Imparable', 'name_en': 'Unstoppable Month', 'desc': 'Racha de 30 dias', 'desc_en': '30-day streak', 'icon': 'fa-fire-alt', 'condition': streak >= 30},
        {'name': 'Guerrero', 'name_en': 'Warrior', 'desc': 'Alcanza nivel 5', 'desc_en': 'Reach level 5', 'icon': 'fa-shield-alt', 'condition': user.level >= 5},
        {'name': 'Campeon', 'name_en': 'Champion', 'desc': 'Alcanza nivel 10', 'desc_en': 'Reach level 10', 'icon': 'fa-crown', 'condition': user.level >= 10},
        {'name': 'Leyenda', 'name_en': 'Legend', 'desc': 'Alcanza nivel 25', 'desc_en': 'Reach level 25', 'icon': 'fa-gem', 'condition': user.level >= 25},
    ]
    for a in achievements:
        a['unlocked'] = a.pop('condition')
    return achievements

def apply_inactivity_penalty(user):
    today_str = datetime.utcnow().date().isoformat()
    if user.last_penalty_date == today_str:
        return
    try:
        docs = db.collection('activities').where('user_id', '==', user.id).get()
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
    try:
        docs = db.collection('activities').where('user_id', '==', user_id).get()
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

def get_activities_in_range(user_id, start_date, end_date):
    all_acts = get_all_activities(user_id)
    return [a for a in all_acts if _get_activity_date(a) and start_date <= _get_activity_date(a) <= end_date]

# =============================================================================
# CHALLENGE HELPERS
# =============================================================================

def get_current_challenges(user_id, user=None):
    today = datetime.utcnow().date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    month_start = today.replace(day=1)
    week_activities = get_activities_in_range(user_id, week_start, week_end)
    month_activities = get_activities_in_range(user_id, month_start, today)
    streak = calculate_streak(user_id) if user else 0
    lang = getattr(user, 'language', 'es') if user else 'es'
    nk = 'name_en' if lang == 'en' else 'name_es'
    dk = 'desc_en' if lang == 'en' else 'desc_es'
    week_num = today.isocalendar()[1]
    month_key = f"{today.year}_{today.month}"

    def progress(tmpl, acts):
        t = tmpl['type']
        if t == 'activity_count': return len(acts)
        if t == 'total_minutes': return sum(a.get('duration', 0) for a in acts)
        if t == 'high_intensity_count': return sum(1 for a in acts if a.get('intensity') == 'high')
        if t == 'unique_types': return len(set(a.get('exercise_type', '') for a in acts))
        if t == 'streak': return streak
        if t == 'total_exp': return sum(a.get('exp_gained', 0) for a in acts)
        return 0

    weekly = []
    for tmpl in WEEKLY_CHALLENGE_TEMPLATES:
        cid = f"{tmpl['key']}_{today.year}_w{week_num}"
        p = progress(tmpl, week_activities)
        weekly.append({'id': cid, 'name': tmpl[nk], 'desc': tmpl[dk], 'target': tmpl['target'],
                       'progress': min(p, tmpl['target']), 'reward_exp': tmpl['reward_exp'],
                       'icon': tmpl['icon'], 'completed': p >= tmpl['target'], 'period': 'weekly'})
    monthly = []
    for tmpl in MONTHLY_CHALLENGE_TEMPLATES:
        cid = f"{tmpl['key']}_{month_key}"
        p = progress(tmpl, month_activities)
        monthly.append({'id': cid, 'name': tmpl[nk], 'desc': tmpl[dk], 'target': tmpl['target'],
                        'progress': min(p, tmpl['target']), 'reward_exp': tmpl['reward_exp'],
                        'icon': tmpl['icon'], 'completed': p >= tmpl['target'], 'period': 'monthly'})
    return weekly, monthly

# =============================================================================
# STATISTICS HELPERS
# =============================================================================

def get_heatmap_data(activities):
    today = datetime.utcnow().date()
    start = today - timedelta(days=364)
    heatmap = {}
    for a in activities:
        d = _get_activity_date(a)
        if d and start <= d <= today:
            key = d.isoformat()
            heatmap[key] = heatmap.get(key, 0) + 1
    return heatmap

def get_weekly_exp_data(activities):
    today = datetime.utcnow().date()
    weeks = []
    for i in range(11, -1, -1):
        ws = today - timedelta(days=today.weekday() + 7 * i)
        we = ws + timedelta(days=6)
        exp = sum(a.get('exp_gained', 0) for a in activities if _get_activity_date(a) and ws <= _get_activity_date(a) <= we)
        weeks.append({'label': ws.strftime('%d/%m'), 'exp': round(exp, 1)})
    return weeks

def get_monthly_summary(activities):
    today = datetime.utcnow().date()
    mes = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
    men = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    summaries = []
    for i in range(5, -1, -1):
        m, y = today.month - i, today.year
        while m <= 0:
            m += 12; y -= 1
        acts = [a for a in activities if _get_activity_date(a) and _get_activity_date(a).month == m and _get_activity_date(a).year == y]
        tm = sum(a.get('duration', 0) for a in acts)
        te = sum(a.get('exp_gained', 0) for a in acts)
        summaries.append({'month_es': mes[m-1], 'month_en': men[m-1], 'year': y,
                          'sessions': len(acts), 'total_minutes': tm, 'total_exp': round(te, 1),
                          'avg_duration': round(tm/len(acts), 1) if acts else 0})
    return summaries

def get_intensity_distribution(activities):
    dist = {'low': 0, 'medium': 0, 'high': 0}
    for a in activities:
        i = a.get('intensity', 'medium')
        if i in dist: dist[i] += 1
    return dist

def check_weigh_in_reminder(activities):
    today = datetime.utcnow().date()
    for a in activities:
        if a.get('weight_recorded'):
            d = _get_activity_date(a)
            if d and (today - d).days <= 7:
                return False
    return True

# =============================================================================
# API AUTH
# =============================================================================

def api_auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.is_authenticated:
            return f(*args, **kwargs)
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]
            try:
                docs = db.collection('users').where('api_token', '==', token).limit(1).get()
                for doc in docs:
                    user = User.from_dict(doc.id, doc.to_dict())
                    login_user(user, remember=False)
                    return f(*args, **kwargs)
            except Exception as e:
                logger.error(f'API token auth error: {e}')
        return jsonify({'error': 'Authentication required'}), 401
    return decorated

# =============================================================================
# WEB ROUTES
# =============================================================================

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
    weight_dates, weight_values = [], []
    for a in reversed(all_activities):
        if a.get('weight_recorded'):
            d = a.get('date')
            weight_dates.append(d.strftime('%d/%m') if hasattr(d, 'strftime') else str(d)[:10])
            weight_values.append(a['weight_recorded'])
    type_counts, total_minutes = {}, 0
    for a in all_activities:
        t = a.get('exercise_type', 'Otro')
        type_counts[t] = type_counts.get(t, 0) + 1
        total_minutes += a.get('duration', 0)
    heatmap = get_heatmap_data(all_activities)
    weekly_challenges, monthly_challenges = get_current_challenges(current_user.id, current_user)
    show_weigh_reminder = check_weigh_in_reminder(all_activities) if all_activities else False
    user_class = PLAYER_CLASSES.get(current_user.player_class) if current_user.player_class else None
    return render_template('dashboard.html', user=current_user, activities=recent_activities,
        exp_for_next=exp_for_next, progress=progress, streak=streak, achievements=achievements,
        unlocked_count=unlocked_count, total_activities=total_activities, total_minutes=total_minutes,
        weight_dates=weight_dates[-20:], weight_values=weight_values[-20:],
        type_labels=list(type_counts.keys()), type_data=list(type_counts.values()),
        heatmap=heatmap, weekly_challenges=weekly_challenges, monthly_challenges=monthly_challenges,
        show_weigh_reminder=show_weigh_reminder, user_class=user_class)

@app.route('/add_activity', methods=['GET', 'POST'])
@login_required
def add_activity():
    if request.method == 'POST':
        exercise_type = request.form.get('exercise_type', '').strip()
        intensity = request.form.get('intensity', 'medium')
        if exercise_type not in VALID_EXERCISE_TYPES:
            flash('Tipo de ejercicio no valido.', 'danger'); return redirect(url_for('add_activity'))
        if intensity not in VALID_INTENSITIES:
            flash('Intensidad no valida.', 'danger'); return redirect(url_for('add_activity'))
        duration = validate_positive_int(request.form.get('duration'), 'duration', 1, 1440)
        if duration is None:
            flash('Duracion debe ser entre 1 y 1440 minutos.', 'danger'); return redirect(url_for('add_activity'))
        weight = validate_positive_float(request.form.get('weight'), 'weight', 10, 500)
        if weight is None:
            flash('Peso debe ser entre 10 y 500 kg.', 'danger'); return redirect(url_for('add_activity'))
        has_evidence = 'evidence' in request.form
        evidence_url = validate_url(request.form.get('evidence_url', ''))
        evidence_photo = validate_base64_image(request.form.get('evidence_photo', ''))
        exercise_details = {}
        if exercise_type in STRENGTH_EXERCISES:
            s = validate_positive_int(request.form.get('sets'), 'sets', 1, 100)
            r = validate_positive_int(request.form.get('reps'), 'reps', 1, 1000)
            w = validate_positive_float(request.form.get('exercise_weight'), 'ew', 0.1, 1000)
            if s: exercise_details['sets'] = s
            if r: exercise_details['reps'] = r
            if w: exercise_details['exercise_weight'] = w
        if exercise_type in DISTANCE_EXERCISES:
            d = validate_positive_float(request.form.get('distance'), 'distance', 0.1, 1000)
            if d: exercise_details['distance_km'] = d
        notes = request.form.get('notes', '').strip()[:500]
        exp_gained = calculate_exp_gain(duration, intensity, has_evidence, current_user, exercise_type)
        if current_user.weight:
            wd = current_user.weight - weight
            if wd > 0: exp_gained *= 1.1
            elif wd < 0: exp_gained *= 0.9
        activity_data = {
            'user_id': current_user.id, 'date': datetime.utcnow(), 'exercise_type': exercise_type,
            'duration': duration, 'intensity': intensity, 'exp_gained': exp_gained,
            'has_evidence': has_evidence, 'evidence_url': evidence_url, 'evidence_photo': evidence_photo,
            'weight_recorded': weight, 'exercise_details': exercise_details, 'notes': notes,
        }
        try:
            db.collection('activities').add(activity_data)
        except Exception as e:
            logger.error(f'Error saving activity: {e}')
            flash('Error al guardar la actividad.', 'danger'); return redirect(url_for('add_activity'))
        current_user.exp += exp_gained
        current_user.weight = weight
        while current_user.exp >= calculate_exp_for_next_level(current_user.level):
            current_user.exp -= calculate_exp_for_next_level(current_user.level)
            current_user.level += 1
            flash(f'Felicitaciones! Has subido al nivel {current_user.level}!', 'success')
        try:
            current_user.update_fields(exp=current_user.exp, weight=current_user.weight, level=current_user.level)
        except Exception as e:
            logger.error(f'Error updating user after activity: {e}')
            flash('Actividad guardada pero hubo un error al actualizar tu perfil.', 'warning')
            return redirect(url_for('dashboard'))
        flash(f'Actividad registrada: +{exp_gained:.1f} EXP', 'success')
        return redirect(url_for('dashboard'))
    return render_template('add_activity.html', user_weight=current_user.weight,
        exercise_types=VALID_EXERCISE_TYPES, strength_exercises=STRENGTH_EXERCISES,
        distance_exercises=DISTANCE_EXERCISES)

@app.route('/edit_activity/<activity_id>', methods=['GET', 'POST'])
@login_required
def edit_activity(activity_id):
    try:
        doc = db.collection('activities').document(activity_id).get()
        if not doc.exists:
            flash('Actividad no encontrada.', 'danger'); return redirect(url_for('history'))
        activity = doc.to_dict(); activity['id'] = doc.id
        if activity.get('user_id') != current_user.id:
            flash('No tienes permiso para editar esta actividad.', 'danger'); return redirect(url_for('history'))
    except Exception as e:
        logger.error(f'Error fetching activity {activity_id}: {e}')
        flash('Error al cargar la actividad.', 'danger'); return redirect(url_for('history'))
    if request.method == 'POST':
        exercise_type = request.form.get('exercise_type', '').strip()
        intensity = request.form.get('intensity', 'medium')
        if exercise_type not in VALID_EXERCISE_TYPES:
            flash('Tipo de ejercicio no valido.', 'danger'); return redirect(url_for('edit_activity', activity_id=activity_id))
        if intensity not in VALID_INTENSITIES:
            flash('Intensidad no valida.', 'danger'); return redirect(url_for('edit_activity', activity_id=activity_id))
        duration = validate_positive_int(request.form.get('duration'), 'duration', 1, 1440)
        if duration is None:
            flash('Duracion debe ser entre 1 y 1440 minutos.', 'danger'); return redirect(url_for('edit_activity', activity_id=activity_id))
        weight = validate_positive_float(request.form.get('weight'), 'weight', 10, 500)
        if weight is None:
            flash('Peso debe ser entre 10 y 500 kg.', 'danger'); return redirect(url_for('edit_activity', activity_id=activity_id))
        has_evidence = 'evidence' in request.form
        evidence_url = validate_url(request.form.get('evidence_url', ''))
        evidence_photo = validate_base64_image(request.form.get('evidence_photo', ''))
        if not evidence_photo:
            evidence_photo = activity.get('evidence_photo', '')
        exercise_details = {}
        if exercise_type in STRENGTH_EXERCISES:
            s = validate_positive_int(request.form.get('sets'), 'sets', 1, 100)
            r = validate_positive_int(request.form.get('reps'), 'reps', 1, 1000)
            w = validate_positive_float(request.form.get('exercise_weight'), 'ew', 0.1, 1000)
            if s: exercise_details['sets'] = s
            if r: exercise_details['reps'] = r
            if w: exercise_details['exercise_weight'] = w
        if exercise_type in DISTANCE_EXERCISES:
            d = validate_positive_float(request.form.get('distance'), 'distance', 0.1, 1000)
            if d: exercise_details['distance_km'] = d
        notes = request.form.get('notes', '').strip()[:500]
        new_exp = calculate_exp_gain(duration, intensity, has_evidence, current_user, exercise_type)
        old_exp = activity.get('exp_gained', 0)
        try:
            db.collection('activities').document(activity_id).update({
                'exercise_type': exercise_type, 'duration': duration, 'intensity': intensity,
                'has_evidence': has_evidence, 'evidence_url': evidence_url, 'evidence_photo': evidence_photo,
                'weight_recorded': weight, 'exp_gained': new_exp, 'exercise_details': exercise_details, 'notes': notes,
            })
            current_user.exp = max(0, current_user.exp + (new_exp - old_exp))
            current_user.update_fields(exp=current_user.exp, weight=weight)
            flash('Actividad actualizada.', 'success')
        except Exception as e:
            logger.error(f'Error updating activity {activity_id}: {e}')
            flash('Error al actualizar la actividad.', 'danger')
        return redirect(url_for('history'))
    return render_template('edit_activity.html', activity=activity,
        exercise_types=VALID_EXERCISE_TYPES, strength_exercises=STRENGTH_EXERCISES,
        distance_exercises=DISTANCE_EXERCISES)

@app.route('/delete_activity/<activity_id>', methods=['POST'])
@login_required
def delete_activity(activity_id):
    try:
        doc = db.collection('activities').document(activity_id).get()
        if not doc.exists:
            flash('Actividad no encontrada.', 'danger'); return redirect(url_for('history'))
        activity = doc.to_dict()
        if activity.get('user_id') != current_user.id:
            flash('No tienes permiso para eliminar esta actividad.', 'danger'); return redirect(url_for('history'))
        exp_lost = activity.get('exp_gained', 0)
        db.collection('activities').document(activity_id).delete()
        try:
            current_user.exp = max(0, current_user.exp - exp_lost)
            current_user.update_fields(exp=current_user.exp)
        except Exception as ue:
            logger.error(f'EXP update failed after delete: {ue}')
            flash('Actividad eliminada pero hubo un error al actualizar tu EXP.', 'warning')
            return redirect(url_for('history'))
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
    weight_dates, weight_values = [], []
    for a in reversed(all_activities):
        if a.get('weight_recorded'):
            d = a.get('date')
            weight_dates.append(d.strftime('%d/%m') if hasattr(d, 'strftime') else str(d)[:10])
            weight_values.append(a['weight_recorded'])
    user_class = PLAYER_CLASSES.get(current_user.player_class) if current_user.player_class else None
    return render_template('profile.html', user=current_user, total_activities=total_activities,
        total_exp=total_exp, total_minutes=total_minutes, streak=streak, achievements=achievements,
        weight_dates=weight_dates[-30:], weight_values=weight_values[-30:], user_class=user_class)

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
    return render_template('history.html', activities=all_activities[start:start+per_page],
        page=page, total_pages=total_pages, total=total)

# --- CLASS SELECTION ---
@app.route('/select-class', methods=['GET', 'POST'])
@login_required
def select_class():
    if request.method == 'POST':
        if current_user.level < 5:
            flash('Necesitas nivel 5 para elegir clase.', 'danger'); return redirect(url_for('select_class'))
        if current_user.class_selected_at:
            try:
                sa = datetime.fromisoformat(current_user.class_selected_at) if isinstance(current_user.class_selected_at, str) else current_user.class_selected_at
                days = (datetime.utcnow() - sa).days if hasattr(sa, 'date') else (datetime.utcnow().date() - sa).days
                if days < 30:
                    flash(f'Solo puedes cambiar de clase una vez al mes. Espera {30-days} dias.', 'warning')
                    return redirect(url_for('select_class'))
            except Exception:
                pass
        chosen = request.form.get('player_class', '').strip()
        if chosen not in PLAYER_CLASSES:
            flash('Clase no valida.', 'danger'); return redirect(url_for('select_class'))
        current_user.update_fields(player_class=chosen, class_selected_at=datetime.utcnow().isoformat())
        cls = PLAYER_CLASSES[chosen]
        lang = current_user.language or 'es'
        flash(f'Has elegido la clase {cls.get(f"name_{lang}", cls["name_es"])}! +30% EXP en tu especialidad.', 'success')
        return redirect(url_for('dashboard'))
    return render_template('select_class.html', user=current_user, classes=PLAYER_CLASSES)

# --- CHALLENGES ---
@app.route('/challenges')
@login_required
def challenges():
    weekly, monthly = get_current_challenges(current_user.id, current_user)
    return render_template('challenges.html', weekly_challenges=weekly, monthly_challenges=monthly, user=current_user)

@app.route('/claim-challenge/<challenge_id>', methods=['POST'])
@login_required
def claim_challenge(challenge_id):
    weekly, monthly = get_current_challenges(current_user.id, current_user)
    target = next((c for c in weekly + monthly if c['id'] == challenge_id), None)
    if not target:
        flash('Reto no encontrado.', 'danger'); return redirect(url_for('challenges'))
    if not target['completed']:
        flash('Aun no has completado este reto.', 'warning'); return redirect(url_for('challenges'))
    if challenge_id in current_user.claimed_challenges:
        flash('Ya reclamaste esta recompensa.', 'info'); return redirect(url_for('challenges'))
    reward = target['reward_exp']
    current_user.exp += reward
    claimed = current_user.claimed_challenges + [challenge_id]
    while current_user.exp >= calculate_exp_for_next_level(current_user.level):
        current_user.exp -= calculate_exp_for_next_level(current_user.level)
        current_user.level += 1
        flash(f'Has subido al nivel {current_user.level}!', 'success')
    current_user.update_fields(exp=current_user.exp, level=current_user.level, claimed_challenges=claimed)
    flash(f'Recompensa reclamada: +{reward} EXP!', 'success')
    return redirect(url_for('challenges'))

# --- STATISTICS ---
@app.route('/stats')
@login_required
def stats():
    all_activities = get_all_activities(current_user.id)
    return render_template('stats.html', user=current_user,
        weekly_exp=get_weekly_exp_data(all_activities), heatmap=get_heatmap_data(all_activities),
        monthly_summary=get_monthly_summary(all_activities), intensity_dist=get_intensity_distribution(all_activities))

# --- SETTINGS ---
@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        language = request.form.get('language', 'es')
        theme = request.form.get('theme', 'dark')
        if language not in ('es', 'en'): language = 'es'
        if theme not in ('dark', 'light'): theme = 'dark'
        current_user.update_fields(language=language, theme=theme)
        resp = make_response(redirect(url_for('settings')))
        resp.set_cookie('lang', language, max_age=365*24*3600)
        resp.set_cookie('theme', theme, max_age=365*24*3600)
        flash('Ajustes guardados.', 'success')
        return resp
    return render_template('settings.html', user=current_user)

# --- AUTH ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        if _is_rate_limited(f'register:{request.remote_addr}', 5, 300):
            flash('Demasiados intentos. Espera unos minutos.', 'danger'); return redirect(url_for('register'))
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        weight_raw = request.form.get('weight')
        email = request.form.get('email', '').strip() or None
        if not username or not re.match(r'^[a-zA-Z0-9_-]{3,30}$', username):
            flash('Usuario debe tener 3-30 caracteres (letras, numeros, _ y -).', 'danger'); return redirect(url_for('register'))
        if not password or len(password) < 8:
            flash('La contrasena debe tener al menos 8 caracteres.', 'danger'); return redirect(url_for('register'))
        weight = validate_positive_float(weight_raw, 'weight', 10, 500)
        if weight is None:
            flash('Peso debe ser entre 10 y 500 kg.', 'danger'); return redirect(url_for('register'))
        if email and not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
            flash('Email no valido.', 'danger'); return redirect(url_for('register'))
        if User.get_by_field('username', username):
            flash('El nombre de usuario ya existe.', 'danger'); return redirect(url_for('register'))
        try:
            doc_ref = db.collection('users').document()
            user = User(id=doc_ref.id, username=username, password=generate_password_hash(password), email=email, weight=weight)
            user.save()
            flash('Registro exitoso! Por favor inicia sesion.', 'success'); return redirect(url_for('login'))
        except Exception as e:
            logger.error(f'Error registering user: {e}')
            flash('Error al registrar. Intenta de nuevo.', 'danger'); return redirect(url_for('register'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if _is_rate_limited(f'login:{request.remote_addr}', 5, 60):
            flash('Demasiados intentos. Espera un minuto.', 'danger'); return redirect(url_for('login'))
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.get_by_field('username', username)
        if user and user.password and check_password_hash(user.password, password):
            login_user(user); return redirect(url_for('dashboard'))
        else:
            logger.warning(f'Failed login for: {username} from {request.remote_addr}')
            flash('Usuario o contrasena incorrectos.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user(); return redirect(url_for('index'))

@app.route('/auth/google', methods=['POST'])
def auth_google():
    id_token = request.form.get('id_token')
    if not id_token:
        flash('Error al iniciar sesion con Google.', 'danger'); return redirect(url_for('login'))
    try:
        decoded_token = firebase_auth.verify_id_token(id_token)
        uid = decoded_token['uid']; email = decoded_token.get('email')
        user = User.get_by_field('google_id', uid)
        if not user:
            if email and User.get_by_field('email', email):
                flash('El email ya esta registrado con otra cuenta.', 'danger'); return redirect(url_for('login'))
            username = email.split('@')[0] if email else uid[:8]
            base_username = username; counter = 1
            while User.get_by_field('username', username):
                username = f"{base_username}{counter}"; counter += 1
            doc_ref = db.collection('users').document()
            user = User(id=doc_ref.id, username=username, email=email, google_id=uid)
            user.save(); login_user(user)
            flash('Cuenta creada! Ingresa tu peso inicial.', 'success'); return redirect(url_for('complete_profile'))
        login_user(user); return redirect(url_for('dashboard'))
    except firebase_admin.exceptions.FirebaseError as e:
        logger.error(f'Firebase auth error: {e}')
        flash('Error al verificar la sesion de Google.', 'danger'); return redirect(url_for('login'))
    except Exception as e:
        logger.error(f'Unexpected auth error: {e}')
        flash('Error al iniciar sesion.', 'danger'); return redirect(url_for('login'))

@app.route('/complete-profile', methods=['GET', 'POST'])
@login_required
def complete_profile():
    if request.method == 'POST':
        weight = validate_positive_float(request.form.get('weight'), 'weight', 10, 500)
        if weight is None:
            flash('Peso debe ser entre 10 y 500 kg.', 'danger'); return redirect(url_for('complete_profile'))
        try:
            current_user.update_fields(weight=weight)
            flash('Perfil completado!', 'success'); return redirect(url_for('dashboard'))
        except Exception as e:
            logger.error(f'Error completing profile: {e}')
            flash('Error al guardar.', 'danger')
    return render_template('complete_profile.html')

# --- PASSWORD RESET ---
@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        if _is_rate_limited(f'reset:{request.remote_addr}', 3, 300):
            flash('Demasiados intentos. Espera unos minutos.', 'danger'); return redirect(url_for('forgot_password'))
        user = User.get_by_field('username', username)
        if user:
            token = secrets.token_urlsafe(32)
            try:
                db.collection('password_resets').add({
                    'user_id': user.id, 'token': token, 'created_at': datetime.utcnow(), 'used': False,
                })
                logger.info(f'Password reset link for {username}: {url_for("reset_password", token=token, _external=True)}')
            except Exception as e:
                logger.error(f'Error creating reset token: {e}')
        flash('Si existe una cuenta con ese usuario, se ha generado un enlace de restablecimiento.', 'info')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        docs = db.collection('password_resets').where('token', '==', token).where('used', '==', False).limit(1).get()
        reset_doc = None
        for doc in docs:
            rd = doc.to_dict(); ca = rd.get('created_at')
            if ca:
                if isinstance(ca, str): ca = datetime.fromisoformat(ca)
                age = datetime.utcnow() - (ca if isinstance(ca, datetime) else datetime.utcnow())
                if age.total_seconds() < 3600:
                    reset_doc = {'id': doc.id, **rd}; break
        if not reset_doc:
            flash('Enlace invalido o expirado.', 'danger'); return redirect(url_for('login'))
    except Exception as e:
        logger.error(f'Error validating reset token: {e}')
        flash('Error al validar el enlace.', 'danger'); return redirect(url_for('login'))
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        if len(password) < 8:
            flash('La contrasena debe tener al menos 8 caracteres.', 'danger')
            return redirect(url_for('reset_password', token=token))
        if password != confirm:
            flash('Las contrasenas no coinciden.', 'danger')
            return redirect(url_for('reset_password', token=token))
        try:
            user = User.get_by_id(reset_doc['user_id'])
            if user:
                user.update_fields(password=generate_password_hash(password))
                db.collection('password_resets').document(reset_doc['id']).update({'used': True})
                flash('Contrasena restablecida. Inicia sesion.', 'success'); return redirect(url_for('login'))
            flash('Usuario no encontrado.', 'danger')
        except Exception as e:
            logger.error(f'Error resetting password: {e}')
            flash('Error al restablecer la contrasena.', 'danger')
        return redirect(url_for('login'))
    return render_template('reset_password.html', token=token)

# =============================================================================
# API REST v1
# =============================================================================

@app.route('/api/v1/auth/login', methods=['POST'])
@csrf.exempt
def api_login():
    data = request.get_json()
    if not data: return jsonify({'error': 'JSON body required'}), 400
    user = User.get_by_field('username', data.get('username', ''))
    if user and user.password and check_password_hash(user.password, data.get('password', '')):
        token = secrets.token_urlsafe(32)
        user.update_fields(api_token=token)
        return jsonify({'token': token, 'user': {'id': user.id, 'username': user.username, 'level': user.level, 'exp': user.exp}})
    return jsonify({'error': 'Invalid credentials'}), 401

@app.route('/api/v1/user', methods=['GET'])
@csrf.exempt
@api_auth_required
def api_get_user():
    u = current_user; cls = PLAYER_CLASSES.get(u.player_class) if u.player_class else None
    return jsonify({'id': u.id, 'username': u.username, 'level': u.level, 'exp': u.exp, 'weight': u.weight,
        'player_class': u.player_class, 'class_info': {'name': cls['name_en'], 'specialty': cls['specialty'], 'bonus': cls['bonus']} if cls else None,
        'language': u.language, 'theme': u.theme})

@app.route('/api/v1/activities', methods=['GET'])
@csrf.exempt
@api_auth_required
def api_get_activities():
    limit = min(request.args.get('limit', 50, type=int), 200)
    activities = get_all_activities(current_user.id)[:limit]
    result = [{'id': a.get('id'), 'date': a.get('date').isoformat() if hasattr(a.get('date'), 'isoformat') else str(a.get('date')),
               'exercise_type': a.get('exercise_type'), 'duration': a.get('duration'), 'intensity': a.get('intensity'),
               'exp_gained': a.get('exp_gained'), 'weight_recorded': a.get('weight_recorded'),
               'exercise_details': a.get('exercise_details', {}), 'notes': a.get('notes', '')} for a in activities]
    return jsonify({'activities': result, 'total': len(result)})

@app.route('/api/v1/activities', methods=['POST'])
@csrf.exempt
@api_auth_required
def api_create_activity():
    data = request.get_json()
    if not data: return jsonify({'error': 'JSON body required'}), 400
    et = data.get('exercise_type', '')
    if et not in VALID_EXERCISE_TYPES: return jsonify({'error': 'Invalid exercise_type'}), 400
    intensity = data.get('intensity', 'medium')
    if intensity not in VALID_INTENSITIES: return jsonify({'error': 'Invalid intensity'}), 400
    duration = data.get('duration')
    if not duration or not isinstance(duration, (int, float)) or duration < 1 or duration > 1440:
        return jsonify({'error': 'duration must be 1-1440'}), 400
    duration = int(duration)
    weight = data.get('weight')
    has_evidence = data.get('has_evidence', False)
    exp_gained = calculate_exp_gain(duration, intensity, has_evidence, current_user, et)
    if weight and current_user.weight:
        wd = current_user.weight - weight
        if wd > 0: exp_gained *= 1.1
        elif wd < 0: exp_gained *= 0.9
    ad = {'user_id': current_user.id, 'date': datetime.utcnow(), 'exercise_type': et, 'duration': duration,
          'intensity': intensity, 'exp_gained': exp_gained, 'has_evidence': has_evidence, 'evidence_url': '',
          'evidence_photo': '', 'weight_recorded': weight or current_user.weight,
          'exercise_details': data.get('exercise_details', {}), 'notes': data.get('notes', '')[:500]}
    try:
        _, doc_ref = db.collection('activities').add(ad)
    except Exception as e:
        logger.error(f'API error saving activity: {e}'); return jsonify({'error': 'Failed to save'}), 500
    current_user.exp += exp_gained
    if weight: current_user.weight = weight
    leveled = False
    while current_user.exp >= calculate_exp_for_next_level(current_user.level):
        current_user.exp -= calculate_exp_for_next_level(current_user.level)
        current_user.level += 1; leveled = True
    try:
        current_user.update_fields(exp=current_user.exp, weight=current_user.weight, level=current_user.level)
    except Exception as e:
        logger.error(f'API error updating user: {e}')
    return jsonify({'activity_id': doc_ref.id, 'exp_gained': exp_gained, 'new_level': current_user.level,
                    'new_exp': current_user.exp, 'leveled_up': leveled}), 201

@app.route('/api/v1/stats', methods=['GET'])
@csrf.exempt
@api_auth_required
def api_get_stats():
    acts = get_all_activities(current_user.id); streak = calculate_streak(current_user.id)
    return jsonify({'level': current_user.level, 'exp': current_user.exp,
        'exp_for_next_level': calculate_exp_for_next_level(current_user.level), 'streak': streak,
        'total_activities': len(acts), 'total_minutes': sum(a.get('duration', 0) for a in acts),
        'total_exp': sum(a.get('exp_gained', 0) for a in acts), 'weight': current_user.weight,
        'intensity_distribution': get_intensity_distribution(acts), 'weekly_exp': get_weekly_exp_data(acts)})

@app.route('/api/v1/challenges', methods=['GET'])
@csrf.exempt
@api_auth_required
def api_get_challenges():
    weekly, monthly = get_current_challenges(current_user.id, current_user)
    claimed = current_user.claimed_challenges or []
    for c in weekly + monthly: c['claimed'] = c['id'] in claimed
    return jsonify({'weekly': weekly, 'monthly': monthly})

# =============================================================================
# ERROR HANDLERS
# =============================================================================

@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(e):
    logger.error(f'Server error: {e}'); return render_template('500.html'), 500

@login_manager.user_loader
def load_user(user_id):
    return User.get_by_id(user_id)

if __name__ == '__main__':
    app.run(debug=os.getenv('FLASK_ENV') == 'development')
