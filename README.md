# Sistema EXP Fitness

Una aplicación web para gamificar tu entrenamiento físico, inspirada en juegos MMO y Solo Leveling.

## Características

- Sistema de niveles y experiencia (EXP)
- Registro de actividades físicas
- Seguimiento de peso
- Sistema de bonificaciones por evidencia visual
- Racha de días consecutivos
- Penalizaciones por inactividad

## Requisitos

- Python 3.8 o superior
- pip (gestor de paquetes de Python)

## Instalación

1. Clonar el repositorio:
```bash
git clone <url-del-repositorio>
cd Sistema-EXP-Fitness
```

2. Crear un entorno virtual (opcional pero recomendado):
```bash
python -m venv venv
source venv/bin/activate  # En Linux/Mac
venv\Scripts\activate     # En Windows
```

3. Instalar las dependencias:
```bash
pip install -r requirements.txt
```

## Uso

1. Iniciar la aplicación:
```bash
python app.py
```

2. Abrir un navegador web y visitar:
```
http://localhost:5000
```

3. Registrar una cuenta nueva y comenzar a registrar tus actividades físicas.

## Cómo Ganar EXP

- Registra tus actividades físicas diarias
- Añade evidencia visual (fotos/videos) para ganar EXP extra
- Mantén una racha de días consecutivos
- Alcanza tus objetivos de peso

## Cómo Perder EXP

- Días sin actividad (-5% de EXP por día)
- Aumento de peso no saludable

## Contribuir

Si deseas contribuir al proyecto:

1. Haz un Fork del repositorio
2. Crea una nueva rama (`git checkout -b feature/nueva-caracteristica`)
3. Haz commit de tus cambios (`git commit -am 'Añadir nueva característica'`)
4. Push a la rama (`git push origin feature/nueva-caracteristica`)
5. Crea un Pull Request
