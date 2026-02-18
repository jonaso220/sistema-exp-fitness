# Analisis de Bugs - Sistema EXP Fitness

Analisis exhaustivo de la webapp. Se encontraron **30 bugs** organizados por severidad.

---

## CRITICOS (6 bugs)

### BUG-01: SECRET_KEY por defecto hardcodeada
- **Archivo:** `app.py:25`
- **Codigo:**
  ```python
  app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-here')
  ```
- **Problema:** Si la variable de entorno `SECRET_KEY` no esta configurada en produccion, Flask usa esta clave debil por defecto. Esto compromete las sesiones, tokens CSRF y toda la seguridad criptografica de la aplicacion.
- **Impacto:** Secuestro de sesiones, falsificacion de tokens CSRF, compromiso total de autenticacion.
- **Fix:** Eliminar el valor por defecto y fallar al arranque si no esta configurada:
  ```python
  app.config['SECRET_KEY'] = os.environ['SECRET_KEY']  # Falla si no existe
  ```

### BUG-02: Modo debug activado en produccion
- **Archivo:** `app.py:769-770`
- **Codigo:**
  ```python
  if __name__ == '__main__':
      app.run(debug=True)
  ```
- **Problema:** El debugger interactivo de Flask expone un REPL de Python completo, muestra codigo fuente en errores, y permite ejecucion arbitraria de codigo.
- **Impacto:** Ejecucion remota de codigo, exposicion de codigo fuente completo.
- **Fix:**
  ```python
  app.run(debug=os.getenv('FLASK_ENV') == 'development')
  ```

### BUG-03: Proteccion CSRF desactivada en endpoint de autenticacion Google
- **Archivo:** `app.py:682-683`
- **Codigo:**
  ```python
  @app.route('/auth/google', methods=['POST'])
  @csrf.exempt
  def auth_google():
  ```
- **Problema:** El endpoint `/auth/google` acepta POST sin verificacion CSRF. Un atacante podria crear un formulario en otro sitio que envie tokens a este endpoint.
- **Impacto:** Ataques CSRF en el flujo de autenticacion de Google.
- **Fix:** Mantener CSRF activo y enviar el token desde el formulario JavaScript del frontend.

### BUG-04: Consultas a Firestore sin limite cargan todo en memoria
- **Archivo:** `app.py:196-199` (calculate_streak), `app.py:287-291` (get_all_activities)
- **Codigo:**
  ```python
  docs = db.collection('activities').where('user_id', '==', user_id).get()  # Sin .limit()
  ```
- **Problema:** Se cargan TODAS las actividades del usuario en memoria. En `dashboard` (linea 333), se obtienen todas pero solo se muestran 10. En `calculate_streak`, se obtienen todas solo para extraer fechas. Un usuario con miles de actividades provocara alto consumo de memoria y latencia.
- **Impacto:** Degradacion de rendimiento, posible OOM en el servidor con usuarios activos.
- **Fix:** Usar `.limit()` y `.order_by()` en las queries segun el caso de uso.

### BUG-05: Transaccion no atomica al guardar actividad + actualizar usuario
- **Archivo:** `app.py:432-454`
- **Problema:** La actividad se guarda en Firestore (linea 433), luego se actualiza el EXP/nivel del usuario (linea 447-452). Si el servidor cae entre ambas operaciones, la actividad existe pero el usuario no recibio el EXP. Igualmente en `delete_activity` (linea 548-551), si el delete funciona pero `update_fields` falla, se pierde la consistencia.
- **Impacto:** Inconsistencia de datos entre actividades y estadisticas del usuario.
- **Fix:** Usar transacciones de Firestore (`db.transaction()`) para asegurar atomicidad.

### BUG-06: Fallo silencioso en update_fields post-actividad
- **Archivo:** `app.py:447-454`
- **Codigo:**
  ```python
  try:
      current_user.update_fields(
          exp=current_user.exp, weight=current_user.weight, level=current_user.level,
      )
  except Exception as e:
      logger.error(f'Error updating user after activity: {e}')
  ```
- **Problema:** Si `update_fields` falla, el error se loguea pero la ejecucion continua normalmente. El usuario ve "Actividad registrada: +X EXP" pero su EXP no se actualizo en la BD. El estado en memoria diverge del estado en Firestore.
- **Impacto:** Datos corruptos: el usuario pierde EXP/nivel sin saberlo.
- **Fix:** Informar al usuario del error y/o reintentar la operacion.

---

## ALTOS (7 bugs)

### BUG-07: Sin rate limiting en login/registro
- **Archivo:** `app.py:658-672` (login), `app.py:614-653` (register)
- **Problema:** No hay limitacion de intentos en los endpoints de autenticacion. Un atacante puede hacer brute force de credenciales o crear cuentas masivamente.
- **Impacto:** Fuerza bruta de contrasenas, spam de cuentas, enumeracion de usuarios.
- **Fix:** Implementar Flask-Limiter:
  ```python
  from flask_limiter import Limiter
  limiter = Limiter(app, key_func=get_remote_address)
  @app.route('/login', methods=['POST'])
  @limiter.limit("5 per minute")
  ```

### BUG-08: Uso peligroso del filtro |safe con datos dinamicos (potencial XSS)
- **Archivo:** `templates/dashboard.html:183-184, 213-214`, `templates/profile.html:96-97`
- **Codigo:**
  ```jinja2
  const weightDates = {{ weight_dates|safe }};
  const typeLabels = {{ type_labels|safe }};
  ```
- **Problema:** `|safe` desactiva el auto-escape de Jinja2. Aunque los datos vienen de `json.dumps()` en el backend (lo cual es seguro actualmente), este patron es fragil. Si alguien modifica el backend y pasa datos sin `json.dumps()`, se convierte en XSS almacenado inmediatamente.
- **Impacto:** XSS almacenado si cambia la logica del backend.
- **Fix:** Usar `|tojson` en lugar de `|safe`:
  ```jinja2
  const weightDates = {{ weight_dates|tojson }};
  ```

### BUG-09: Sin validacion de caracteres en username
- **Archivo:** `app.py:617-623`
- **Codigo:**
  ```python
  username = request.form.get('username', '').strip()
  if not username or len(username) < 3 or len(username) > 30:
  ```
- **Problema:** Solo valida longitud, no caracteres. Un username puede contener `<script>`, caracteres Unicode, espacios, etc. Aunque Jinja2 escapa la salida, es una defensa fragil.
- **Impacto:** Posible XSS almacenado si se cambia el template; usernames confusos.
- **Fix:**
  ```python
  import re
  if not re.match(r'^[a-zA-Z0-9_-]{3,30}$', username):
      flash('Nombre de usuario solo puede contener letras, numeros, - y _', 'danger')
  ```

### BUG-10: Contrasena demasiado debil (minimo 6 caracteres)
- **Archivo:** `app.py:625-627`
- **Problema:** Solo se requieren 6 caracteres, sin requisitos de complejidad. Muy por debajo de las recomendaciones NIST (12+ caracteres).
- **Impacto:** Contrasenas vulnerables a ataques de diccionario.

### BUG-11: Sin headers de seguridad HTTP
- **Archivo:** `app.py` (ausencia global)
- **Problema:** No se configuran headers de seguridad:
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY` (proteccion clickjacking)
  - `Content-Security-Policy`
  - `Strict-Transport-Security` (HSTS)
- **Impacto:** Clickjacking, MIME sniffing, ataques XSS facilitados.
- **Fix:** Agregar `@app.after_request` con los headers.

### BUG-12: Fuga de formularios DOM en autenticacion Google
- **Archivo:** `templates/login.html:60-61`, `templates/register.html:68-69`
- **Codigo:**
  ```javascript
  form.appendChild(input);
  document.body.appendChild(form);
  form.submit();
  ```
- **Problema:** Se crea un formulario en el DOM para enviar el token de Google, pero nunca se elimina. Multiples intentos de login crean formularios huerfanos acumulandose.
- **Impacto:** Memory leak en el navegador; bloat del DOM.
- **Fix:** Limpiar el formulario despues del envio.

### BUG-13: Promesas sin manejar completamente en Google Auth
- **Archivo:** `templates/login.html:45-67`, `templates/register.html:53-75`
- **Problema:** En la cadena `.then()` de Firebase auth, si `result.user` es undefined o `getIdToken()` falla de manera inesperada, no hay recuperacion. `error.message` podria ser undefined en algunos errores.
- **Impacto:** Usuario se queda atascado en la pagina de login sin feedback claro.

---

## MEDIOS (10 bugs)

### BUG-14: Error 404 muestra index.html en vez de pagina de error
- **Archivo:** `app.py:752-754`
- **Codigo:**
  ```python
  @app.errorhandler(404)
  def not_found(e):
      return render_template('index.html'), 404
  ```
- **Problema:** El usuario ve la landing page normal cuando accede a una URL invalida, pero con codigo 404. Es confuso porque parece que la pagina cargo bien.
- **Impacto:** UX confusa; el usuario no sabe que la pagina no existe.
- **Fix:** Crear un template `404.html` dedicado.

### BUG-15: Error 500 hace redirect en vez de mostrar error
- **Archivo:** `app.py:757-761`
- **Codigo:**
  ```python
  @app.errorhandler(500)
  def server_error(e):
      return redirect(url_for('index'))
  ```
- **Problema:** Un redirect en un error 500 pierde el contexto del error y el codigo HTTP cambia a 302. El flash message puede perderse si la sesion esta corrupta (que puede ser la causa del 500).
- **Impacto:** Errores del servidor quedan ocultos al usuario.

### BUG-16: Fallos silenciosos en consultas de base de datos
- **Archivos:** `app.py:100-107` (get_by_field), `app.py:284-294` (get_all_activities), `app.py:192-203` (calculate_streak)
- **Problema:** Todos los errores de BD retornan valores por defecto (None, [], 0) sin distincion entre "no encontrado" y "error de BD". Esto causa:
  - Login siempre falla si Firestore esta caido (pero el mensaje dice "usuario incorrecto")
  - Streak se muestra como 0 si hay error de BD (el usuario pierde su racha visual)
  - Dashboard se muestra vacio sin actividades en vez de mostrar un error
- **Impacto:** Errores de infraestructura se ocultan como comportamiento "normal".

### BUG-17: Input de peso sin min/max en complete_profile
- **Archivo:** `templates/complete_profile.html:16-18`
- **Codigo:**
  ```html
  <input type="number" class="form-control" id="weight" name="weight" step="0.1" required>
  ```
- **Problema:** A diferencia de `register.html` y `add_activity.html`, este input no tiene `min="10"` ni `max="500"`. El usuario puede enviar valores invalidos (negativos, 0, 50000) que el backend rechazara.
- **Impacto:** UX pobre: el usuario puede enviar el formulario y recibir un error inesperado.
- **Fix:** Agregar `min="10" max="500"`.

### BUG-18: Doble submit en formularios de actividad
- **Archivo:** `templates/add_activity.html`, `templates/edit_activity.html`
- **Problema:** El boton de submit no se deshabilita durante el envio. En redes lentas, el usuario puede hacer click multiples veces creando actividades duplicadas.
- **Impacto:** Actividades duplicadas, EXP duplicado.
- **Fix:** Deshabilitar el boton al hacer submit y mostrar indicador de carga.

### BUG-19: Formato de fecha inconsistente entre paginas
- **Archivo:** `templates/dashboard.html:131-135` vs `templates/history.html:29-33`
- **Problema:** En dashboard se muestra `%d/%m/%Y` (sin hora), en history se muestra `%d/%m/%Y %H:%M` (con hora). El fallback sin strftime puede mostrar representaciones internas del objeto datetime.
- **Impacto:** UX inconsistente.

### BUG-20: CDN sin Subresource Integrity (SRI)
- **Archivo:** `templates/base.html:12-14, 213`
- **Problema:** Bootstrap, Font Awesome y Chart.js se cargan desde CDN sin atributo `integrity`. Si el CDN es comprometido, se podria inyectar codigo malicioso.
- **Impacto:** Supply chain attack via CDN comprometido.

### BUG-21: Sin timeout de sesion
- **Archivo:** `app.py` (ausencia)
- **Problema:** Las sesiones de usuario nunca expiran. Una sesion robada permanece valida indefinidamente.
- **Impacto:** Sesiones hijackeadas activas para siempre.
- **Fix:**
  ```python
  app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)
  ```

### BUG-22: Codigo de validacion de formularios duplicado en 5 templates
- **Archivos:** `add_activity.html`, `edit_activity.html`, `login.html`, `register.html`, `complete_profile.html`
- **Problema:** El mismo bloque JavaScript de validacion Bootstrap se repite en 5 templates. Deberia estar en `base.html` o en un archivo JS compartido.
- **Impacto:** Mantenibilidad; cambios deben hacerse en 5 lugares.

### BUG-23: Reglas de Firestore no visibles/no versionadas
- **Problema:** No hay archivo `firestore.rules` en el repositorio. Si las reglas son permisivas, la BD es accesible directamente desde el cliente usando la configuracion de Firebase expuesta en `base.html:217-224`.
- **Impacto:** Acceso no autorizado a datos si las reglas son laxas.

---

## BAJOS (7 bugs)

### BUG-24: firebase-admin sin version fija
- **Archivo:** `requirements.txt`
- **Codigo:** `firebase-admin>=6.0.0`
- **Problema:** Sin limite superior de version. Una actualizacion automatica podria romper la app.

### BUG-25: Sin logging de eventos de seguridad
- **Archivo:** `app.py:658-672`
- **Problema:** No se loguean intentos fallidos de login, cambios de contrasena, ni accesos no autorizados. Imposible detectar ataques.

### BUG-26: Validacion de URL insuficiente
- **Archivo:** `app.py:150-160`
- **Problema:** Solo valida scheme y netloc. No protege contra URLs maliciosas con payloads en path/query (ej: `https://evil.com/<script>alert(1)</script>`). La URL se almacena y podria renderizarse sin escape.

### BUG-27: Animacion de barra de progreso con delay arbitrario
- **Archivo:** `templates/dashboard.html:176-180`
- **Problema:** `setTimeout` con 100ms hardcodeado. No valida que `data-progress` sea numerico. En dispositivos lentos puede no animarse correctamente.

### BUG-28: Sin mecanismo de bloqueo de cuenta
- **Problema:** No hay lockout despues de N intentos fallidos. Combinado con BUG-07, facilita brute force.

### BUG-29: Penalidad por inactividad falla silenciosamente
- **Archivo:** `app.py:250-258`
- **Problema:** Si la consulta de actividades falla, la funcion retorna sin aplicar penalidad ni notificar al usuario. El usuario cree que no tiene penalidad cuando en realidad hubo un error.

### BUG-30: delete_activity puede descontar EXP aunque el delete falle
- **Archivo:** `app.py:547-551`
- **Problema:** Todo esta dentro del mismo try/except. Si `db.collection('activities').document(activity_id).delete()` tiene exito pero `update_fields` falla, el EXP no se descuenta. Si el delete falla parcialmente, el flujo puede continuar descontando EXP sin haber eliminado la actividad.

---

## Resumen

| Severidad | Cantidad | Bugs |
|-----------|----------|------|
| **Critico** | 6 | BUG-01 a BUG-06 |
| **Alto** | 7 | BUG-07 a BUG-13 |
| **Medio** | 10 | BUG-14 a BUG-23 |
| **Bajo** | 7 | BUG-24 a BUG-30 |
| **Total** | **30** | |

## Prioridad de correccion recomendada

1. **Inmediato:** BUG-01 (SECRET_KEY), BUG-02 (debug mode), BUG-03 (CSRF)
2. **Urgente:** BUG-07 (rate limiting), BUG-05 (transacciones), BUG-08 (XSS)
3. **Importante:** BUG-04 (queries), BUG-11 (headers), BUG-14/15 (error pages)
4. **Planificado:** Resto de bugs medios y bajos
