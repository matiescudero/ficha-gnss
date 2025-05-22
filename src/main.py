import os
from datetime import datetime, time
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from jinja2 import Environment, FileSystemLoader
import pymap3d as pm
import re, utm
import sys
import base64

# — Carga .env —
load_dotenv()

DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")

SQLALCHEMY_DATABASE_URL = (
    f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)
engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_size=5, max_overflow=10)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

def fetch_one(sql: str, params: dict) -> dict:
    with SessionLocal() as session:
        row = session.execute(text(sql), params).mappings().first()
        return dict(row) if row else {}

def fetch_scalar(sql: str, params: dict):
    with SessionLocal() as session:
        return session.execute(text(sql), params).scalar()

def fetch_equipment_from_amsur_info(db_session, station: str, fecha_str: str):
    """
    Para la estación y fecha dadas:
      1) Encuentra el registro cuya ventana [from_date, to_date] cubre esa fecha -> ACTUAL
      2) Busca hacia atrás el primer cambio de receptor distinto -> PREVIO_RECEPTOR
      3) Busca hacia atrás el primer cambio de antena distinto   -> PREVIO_ANTENA
      Si no hay previo, deja None.
    """
    fecha_dt = datetime.strptime(fecha_str, "%Y-%m-%d")

    # 1) Registro activo en esa fecha
    sql_actual = text("""
        SELECT id, from_date, receiver_type, antenna_type
        FROM amsur_info
        WHERE station = :station
          AND from_date <= :fecha_dt
          AND to_date   >= :fecha_dt
        ORDER BY from_date DESC
        LIMIT 1
    """)
    act = db_session.execute(sql_actual, {
        "station": station,
        "fecha_dt": fecha_dt
    }).mappings().first()
    if not act:
        raise ValueError(f"No hay equipamiento activo para {station} en {fecha_str}")

    rec_act_type = act["receiver_type"].strip()
    rec_act_date = act["from_date"].date().isoformat()
    ant_act_type = act["antenna_type"].strip()
    ant_act_date = rec_act_date  # suelen coincidir

    # 2) Primer cambio anterior de receptor
    sql_prev_rec = text("""
        SELECT from_date, receiver_type
        FROM amsur_info
        WHERE station = :station
          AND from_date < :act_from
          AND receiver_type <> :rec_act_type
        ORDER BY from_date DESC
        LIMIT 1
    """)
    prev_rec = db_session.execute(sql_prev_rec, {
        "station": station,
        "act_from": act["from_date"],
        "rec_act_type": rec_act_type
    }).mappings().first()

    rec_prev_type = prev_rec["receiver_type"].strip() if prev_rec else None
    rec_prev_date = prev_rec["from_date"].date().isoformat() if prev_rec else None

    # 3) Primer cambio anterior de antena
    sql_prev_ant = text("""
        SELECT from_date, antenna_type
        FROM amsur_info
        WHERE station = :station
          AND from_date < :act_from
          AND antenna_type <> :ant_act_type
        ORDER BY from_date DESC
        LIMIT 1
    """)
    prev_ant = db_session.execute(sql_prev_ant, {
        "station": station,
        "act_from": act["from_date"],
        "ant_act_type": ant_act_type
    }).mappings().first()

    ant_prev_type = prev_ant["antenna_type"].strip() if prev_ant else None
    ant_prev_date = prev_ant["from_date"].date().isoformat() if prev_ant else None

    return {
        "receiver_actual_date":   rec_act_date,
        "receiver_actual_type":   rec_act_type,
        "receiver_previous_date": rec_prev_date,
        "receiver_previous_type": rec_prev_type,
        "antenna_actual_date":    ant_act_date,
        "antenna_actual_type":    ant_act_type,
        "antenna_previous_date":  ant_prev_date,
        "antenna_previous_type":  ant_prev_type,
    }


ELLIPSOID = pm.Ellipsoid(
    semimajor_axis=6378137.0,
    semiminor_axis=6356752.314140356
)

def dec2dms(deg):
    sign = -1 if deg < 0 else 1
    deg = abs(deg)
    d = int(deg)
    m = int((deg - d) * 60)
    s = (deg - d - m/60) * 3600
    return d, m, s, sign

def fmt_lat(lat):
    d, m, s, sign = dec2dms(lat)
    hemi = "S" if sign < 0 else "N"
    return f"{d}°{m}′{s:.5f}″{hemi}"

def fmt_lon(lon):
    d, m, s, sign = dec2dms(lon)
    hemi = "O" if sign < 0 else "E"
    return f"{d}°{m}′{s:.5f}″{hemi}"

def format_epoca_from_yearmonth(yearmonth: int) -> str:
    yy, mm = divmod(yearmonth, 100)
    frac = "50" if mm == 7 else "00"
    return f"{yy}.{frac}"

def format_epoca_from_filename(fname: str) -> str:
    # busca algo como "_2024_00." o "_2024_50."
    m = re.search(r'_(\d{4})_(\d{2})\.', fname)
    if not m:
        return None
    yy, code = m.group(1), m.group(2)
    frac = "50" if code == "50" else "00"
    return f"{yy}.{frac}"

def fetch_coords_from_validation(db_session, station: str, fecha_str: str):
    """
    - Coge el último registro en validation para station con epoch <= fecha_str + 12:00
    - Extrae x,y,z,constellations,file,yearmonth
    - Calcula lat/lon/h con pm.ecef2geodetic y formatea en DMS
    - Saca la epoca primero del filename, si falla usa el yearmonth numérico
    """

    fecha = datetime.strptime(fecha_str, "%Y-%m-%d").date()
    target_ts = datetime.combine(fecha, time(12,0,0))

    sql = text("""
        SELECT file, x, y, z, yearmonth, constellations
          FROM validation
         WHERE station = :station
           AND epoch <= :target_ts
         ORDER BY epoch DESC
         LIMIT 1
    """)
    row = db_session.execute(sql, {"station": station, "target_ts": target_ts}).mappings().first()
    if not row:
        raise ValueError(f"No hay datos para {station} antes de {target_ts}")

    fname        = row["file"] or ""
    x, y, z      = row["x"], row["y"], row["z"]
    ym           = row["yearmonth"]
    constel      = row["constellations"] or "-"

    # 1) calculo geodésico
    lat_dec, lon_dec, h = pm.ecef2geodetic(x, y, z, ell=ELLIPSOID, deg=True)

    # 2) formateo la epoca
    epoca = format_epoca_from_filename(fname) or format_epoca_from_yearmonth(ym)
    
    # 3) Conversión a UTM
    easting, northing, zone_number, zone_letter = utm.from_latlon(lat_dec, lon_dec)
    hemisphere = "S" if lat_dec < 0 else "N"
    zona_utm = f"{zone_number}{hemisphere}"

    return {
        "ecef_x":         f"{x:.5f}",
        "ecef_y":         f"{y:.5f}",
        "ecef_z":         f"{z:.5f}",
        "epoca":          epoca,
        "constelaciones": constel,
        "lat":            fmt_lat(lat_dec),
        "lon":            fmt_lon(lon_dec),
        "h":              f"{h:.3f}",
        "zona_utm":       zona_utm,
        "utm_e":          f"{easting:.3f}",
        "utm_n":          f"{northing:.3f}",
    }

def fetch_sum_metadata(db_session, station: str, fecha_str: str):
    """
    De public.sum, obtiene:
      - el último registro cuya date <= fecha_str
      - de ahí saca dome→numero_domo
      - e, n, u → prec_e, prec_n, prec_u
    Si no hay nada, devuelve '-' para cada campo.
    """
    fecha = datetime.strptime(fecha_str, "%Y-%m-%d").date()
    sql = text("""
        SELECT dome, e, n, u
        FROM public.sum
        WHERE station = :station
          AND date <= :fecha
        ORDER BY date DESC
        LIMIT 1
    """)
    row = db_session.execute(sql, {"station": station, "fecha": fecha}).mappings().first()
    if not row:
        return {
            "numero_domo": "-",
            "prec_e":        "-",
            "prec_n":        "-",
            "prec_u":        "-"
        }

    return {
        "numero_domo": row["dome"] or "-",
        "prec_e":      f"{row['e']:.4f}",
        "prec_n":      f"{row['n']:.4f}",
        "prec_u":      f"{row['u']:.4f}",
    }

# ——— Campos fijos ———
FIXED_FIELDS = {
    # Sección Información
    "id_estacion": "USCL00CHL",
    "fecha_servicio": "2023-01-15",
    "altura": "0.0343",
    "referencia": "ARP",
    "clasificacion": "S K P R",
    "rnx2": None,
    "intervalo_grabacion": "30S",
    "sesion": "diario",
    "constelaciones": "GPS+GLO+GAL+BDS+SBAS",
    "logfile": "https://geodesychile.usach.cl/infraestructura-geodesica/usach-rinex-gnss",
    "archivos_rnx3": "https://geodesychile.usach.cl/infraestructura-geodesica/usach-rinex-gnss",
    "archivos_rnx2": None,
    "archivos_navegacion": None,
    "red_nacional": None,
    "red_continental": "SIRGASCON",
    "red_global": "IGS",
    "propietario": "USACH",
    "gestor": "USACH",
    "ciudad": "Santiago",
    "region": "Metropolitana",
    "pais": "Chile",

    # Sección Coordenadas (se completarán luego)
    "sistema":   "SIRGASCON",
    "marco":     "ADELA (https://doi.org/10.1515/jogs-2022-0173)",
    "elipsoide": "GRS80",

    # Pie de página
    "ahora": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
}

def inline_img(path):
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    ext = path.rsplit(".",1)[1]
    return f"data:image/{ext};base64,{data}"

def build_context(station: str, fecha: str) -> dict:
    """
    Arma el contexto para la plantilla:
      - Datos fijos
      - Coordenadas + UTM + epoca
      - Precisiones + domo
      - Equipamiento
      - Imágenes en Base64 para usach_logo y cpag_logo
      - Parámetros de consulta (estación y fecha)
    """
    # 1) consulta a la BDD
    with SessionLocal() as db:
        coords = fetch_coords_from_validation(db, station, fecha)
        summ   = fetch_sum_metadata(db, station, fecha)
        equip  = fetch_equipment_from_amsur_info(db, station, fecha)

    # 2) incrustar logos
    logo_usach = inline_img("static/usach_logo.png")
    logo_cpag  = inline_img("static/cpag_logo.png")

    # 3) combinar todo el contexto
    context = {
        **FIXED_FIELDS,
        **coords,
        **summ,
        **equip,
        # imágenes embebidas
        "logo_usach": logo_usach,
        "logo_cpag":  logo_cpag,
        # parámetros de consulta
        "estacion":      station,
        "fecha_consulta": fecha,
    }
    return context


def main():
    estacion = input("Estación (p.ej. USCL): ").strip().upper()
    fecha    = input("Fecha (YYYY-MM-DD): ").strip()
    try:
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        print("❌ Formato de fecha inválido, debe ser YYYY-MM-DD.", file=sys.stderr)
        sys.exit(1)

    # Usamos build_context para traer TODO + logos inline
    context = build_context(estacion, fecha)

    env = Environment(
        loader=FileSystemLoader("templates"),
        auto_reload=True,
        cache_size=0
    )
    tpl = env.get_template("ficha.html")
    html_out = tpl.render(**context)

    output_path = f"ficha_{estacion}_{fecha}.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    print("Generada →", output_path)


if __name__ == "__main__":
    main()