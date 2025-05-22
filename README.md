# Ficha GNSS Automática

Este proyecto genera automáticamente una ficha PDF de estación GNSS basada en datos almacenados en una base de datos PostgreSQL.

## Requisitos

- **Linux** 
- Python 3.8+
- wkhtmltopdf (para generación de PDF)

La base de datos PostgreSQL (`geo`) debe existir y estar accesible:

```
POSTGRES_DB=geo
POSTGRES_USER=postgres
POSTGRES_PASSWORD=dsa1233
```

## Instalación

1. Clonar el repositorio:
   ```bash
   git clone <tu_repo_url>
   cd <tu_repositorio>
   ```

2. Crear y activar el entorno virtual:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. Instala dependencias:
   ```bash
   pip install -r requirements.txt
   ```

## Uso

Ejecutar la generación de ficha:

```bash
python -m src.main
```

Sigue los prompts:
- **Estación** (p.ej. `USCL`)
- **Fecha** (YYYY-MM-DD)

Se generará:
- `ficha_{ESTACION}_{FECHA}.pdf`

## Dependencias clave

- `sqlalchemy`
- `jinja2`
- `pymap3d`
- `utm`
- `wkhtmltopdf` (debe estar instalado globalmente)

