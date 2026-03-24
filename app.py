import os, hashlib, requests, time, logging
from dotenv import load_dotenv

load_dotenv()
import feedparser
import concurrent.futures
from datetime import datetime, timedelta, date, timezone
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    jsonify,
    send_from_directory,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, text
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# ── LOGGING ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("mapping_dissonance")

try:
    from birdnetlib import Recording
    from birdnetlib.analyzer import Analyzer

    BIRDNET_AVAILABLE = True
except Exception:
    Recording = None
    Analyzer = None
    BIRDNET_AVAILABLE = False

try:
    from flask_compress import Compress as FlaskCompress
except Exception:
    FlaskCompress = None


try:
    import algosdk
    from algosdk import mnemonic as algomnemo, transaction as algotxn
    from algosdk.v2client import algod as algoclient

    ALGORAND_AVAILABLE = True
except Exception:
    ALGORAND_AVAILABLE = False

try:
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration
    SENTRY_AVAILABLE = True
except Exception:
    SENTRY_AVAILABLE = False

try:
    from flask_wtf.csrf import CSRFProtect
    CSRF_AVAILABLE = True
except Exception:
    CSRFProtect = None
    CSRF_AVAILABLE = False

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    LIMITER_AVAILABLE = True
except Exception:
    Limiter = None
    LIMITER_AVAILABLE = False

from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=2, x_proto=1, x_host=1, x_prefix=1)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-production")

# Render/Heroku expose DATABASE_URL with legacy postgres:// prefix; SQLAlchemy 2.x requires postgresql://
_db_url = os.environ.get("DATABASE_URL") or f"sqlite:///{os.path.join(app.instance_path, 'dissonance_ledger.db')}"
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _db_url

app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "uploads")
app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=30)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024  # 15 MB upload limit
app.config["WTF_CSRF_TIME_LIMIT"] = 3600  # 1 hour CSRF token validity

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# ── SENTRY ────────────────────────────────────────────────────────────────────
_sentry_dsn = os.environ.get("SENTRY_DSN", "")
if SENTRY_AVAILABLE and _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,
    )
    logger.info("Sentry initialised")

if FlaskCompress:
    FlaskCompress(app)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# ── CSRF ──────────────────────────────────────────────────────────────────────
csrf = CSRFProtect(app) if CSRF_AVAILABLE else None

# ── RATE LIMITER ──────────────────────────────────────────────────────────────
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],          # no global limit — only explicit per-route limits
    storage_uri="memory://",
) if LIMITER_AVAILABLE else None

# ── ALGORAND ──────────────────────────────────────────────────────────────────

ALGOD_URL = "https://testnet-api.algonode.cloud"
ALGOD_TOKEN = ""  # AlgoNode public node — no token required


def _algorand_notarise(note_text):
    """Submit a 0-ALGO self-payment on Algorand Testnet; return TXID or None."""
    if not ALGORAND_AVAILABLE:
        return None
    mnemonic_phrase = os.environ.get("ALGORAND_MNEMONIC", "")
    if not mnemonic_phrase:
        return None
    try:
        private_key = algomnemo.to_private_key(mnemonic_phrase)
        address = algosdk.account.address_from_private_key(private_key)
        client = algoclient.AlgodClient(ALGOD_TOKEN, ALGOD_URL)
        params = client.suggested_params()
        txn = algotxn.PaymentTxn(
            sender=address,
            sp=params,
            receiver=address,
            amt=0,
            note=note_text.encode("utf-8"),
        )
        signed = txn.sign(private_key)
        txid = client.send_transaction(signed)
        # TXID is valid immediately; skip blocking wait_for_confirmation
        return txid
    except Exception as e:
        logger.warning("Algorand notarisation failed: %s", e)
        return None


# ── MODELS ────────────────────────────────────────────────────────────────────


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False)
    password = db.Column(db.String(256), nullable=False)


class ArchiveEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    species_common = db.Column(db.String(100))
    species_sci = db.Column(db.String(100))
    species_name_merlin = db.Column(db.String(200))
    confidence = db.Column(db.Float)
    location_name = db.Column(db.String(200))
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    recording_time_of_day = db.Column(db.String(20))
    recording_date = db.Column(db.Date)
    file_path = db.Column(db.String(200))
    prev_hash = db.Column(db.String(64))
    current_hash = db.Column(db.String(64), unique=True)
    iucn_status = db.Column(db.String(10), default="LC")
    care_signatures = db.relationship("CareSignature", backref="entry", lazy=True, cascade="all, delete-orphan")


class CareSignature(db.Model):
    """Community acknowledgment — Algorand Testnet TXID or SHA-256 fallback."""

    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("archive_entry.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    care_token = db.Column(
        db.String(100), nullable=False
    )  # Algorand TXID (52 chars) or SHA-256 (64 chars)
    statement = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    location_verified = db.Column(db.Boolean, default=False)
    __table_args__ = (db.UniqueConstraint("entry_id", "user_id"),)
    transaction_logs = db.relationship("UserTransactionLog", backref="care_signature", lazy=True, cascade="all, delete-orphan")


class UserTransactionLog(db.Model):
    """Permanent log of every user's Transaction IDs and Care Ledger IDs."""

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    care_signature_id = db.Column(db.Integer, db.ForeignKey("care_signature.id"), nullable=False, index=True)
    txid = db.Column(db.String(100), nullable=False)  # Algorand TXID or SHA-256 witness token
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class FolkloreEntry(db.Model):
    """Community-contributed ecological folklore and place memory."""

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    location_name = db.Column(db.String(200))
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    prev_hash = db.Column(db.String(64))
    current_hash = db.Column(db.String(64), unique=True)
    witnesses = db.relationship("FolkloreWitness", backref="folklore_entry", lazy=True, cascade="all, delete-orphan")


class FolkloreWitness(db.Model):
    """Community acknowledgment — I have also heard this story."""

    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("folklore_entry.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    token = db.Column(db.String(100), nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.UniqueConstraint("entry_id", "user_id"),)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ── ENGINES ───────────────────────────────────────────────────────────────────

# BirdNET is loaded lazily inside upload() — not at startup — to avoid each
# Gunicorn worker pre-loading the ~400 MB TensorFlow model unnecessarily.
analyzer = None

# ── HELPERS ───────────────────────────────────────────────────────────────────

CARE_STATEMENT = (
    "I am local to this area. I have heard these sounds. "
    "I acknowledge the urgency of its conservation."
)


def get_coords(location_name):
    try:
        url = f"https://nominatim.openstreetmap.org/search?q={location_name}+Delhi&format=json&limit=1"
        res = requests.get(
            url, headers={"User-Agent": "MappingDissonanceBot/1.0"}, timeout=5
        ).json()
        if res:
            return float(res[0]["lat"]), float(res[0]["lon"])
    except Exception:
        pass
    return 28.6139, 77.2090


def entry_to_dict(e, care_count=0):
    return {
        "id": e.id,
        "species_common": e.species_common or "Unknown",
        "species_sci": e.species_sci or "",
        "species_name_merlin": e.species_name_merlin or "",
        "confidence": round(e.confidence, 3) if e.confidence else 0,
        "location_name": e.location_name or "",
        "lat": e.lat or 0,
        "lng": e.lng or 0,
        "timestamp": e.timestamp.strftime("%d %b %Y, %H:%M") if e.timestamp else "",
        "recording_time_of_day": e.recording_time_of_day or "",
        "file_path": e.file_path or "",
        "current_hash": e.current_hash or "",
        "care_count": care_count,
        "iucn": e.iucn_status or "LC",
    }


def folklore_to_dict(fe, witness_count=0):
    return {
        "id": fe.id,
        "title": fe.title,
        "location_name": fe.location_name or "",
        "lat": fe.lat or 0,
        "lng": fe.lng or 0,
        "timestamp": fe.timestamp.strftime("%d %b %Y") if fe.timestamp else "",
        "witness_count": witness_count,
    }


# ── NEWS: CURATED RSS FEEDS ───────────────────────────────────────────────────

CURATED_RSS = [
    (
        "Mongabay India · Delhi",
        "https://india.mongabay.com/feed/?post_type=post&feedtype=bulletpoints&location=delhi",
    ),
    (
        "Mongabay India · Haryana",
        "https://india.mongabay.com/feed/?post_type=post&feedtype=bulletpoints&location=haryana",
    ),
    (
        "Mongabay India · Forests",
        "https://india.mongabay.com/feed/?post_type=post&feedtype=bulletpoints&topic=forests",
    ),
    ("Mongabay India", "https://india.mongabay.com/feed/"),
    ("Down To Earth", "https://www.downtoearth.org.in/feed"),
    ("The Wire Science", "https://science.thewire.in/feed/"),
]


def _loc_keywords(location):
    loc = location.lower().strip()
    words = [w for w in loc.split() if len(w) > 3]
    return list(dict.fromkeys([loc] + words))


def _rss_score(entry, kws):
    text = (
        entry.get("title", "")
        + " "
        + entry.get("summary", "")
        + " "
        + " ".join(t.get("term", "") for t in entry.get("tags", []))
    ).lower()
    return sum(1 for k in kws if k in text)


def _fetch_rss(source_name, url, kws):
    try:
        feed = feedparser.parse(url)
        results = []
        for e in feed.entries:
            score = _rss_score(e, kws)
            if score > 0:
                results.append(
                    {
                        "title": e.get("title", ""),
                        "url": e.get("link", ""),
                        "source": {"name": source_name},
                        "publishedAt": e.get("published", ""),
                        "_score": score,
                    }
                )
        return results
    except Exception:
        return []


# ── SECURITY HEADERS + CACHE CONTROL ─────────────────────────────────────────

@app.after_request
def apply_headers(response):
    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    # Cache-Control: static audio files are immutable (content-addressed filenames)
    if request.path.startswith("/audio/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=86400"
    else:
        response.headers["Cache-Control"] = "no-cache"
    return response


# ── HEALTH CHECK ──────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    """Liveness probe for Render / Dokploy. Returns 200 when app is up."""
    return jsonify({"status": "ok", "birdnet": BIRDNET_AVAILABLE, "algorand": ALGORAND_AVAILABLE}), 200


# ── ROBOTS.TXT ────────────────────────────────────────────────────────────────

@app.route("/robots.txt")
def robots():
    return app.response_class("User-agent: *\nDisallow: /upload\nDisallow: /care/\nDisallow: /folklore/\n", mimetype="text/plain")


# ── ROUTES ────────────────────────────────────────────────────────────────────

# ── ARAVALLI / DELHI RIDGE BIRD CHECKLIST ──────────────────
BIRDS = [
    # ── LC ──
    {
        "common": "Common Babbler",
        "scientific": "Argya caudata",
        "hindi": "Saat Bhai",
        "rajasthani": "Sat Bhaiya",
        "tamil": "Varigal Kuruvi",
        "iucn": "LC",
    },
    {
        "common": "Indian Robin",
        "scientific": "Copsychus fulicatus",
        "hindi": "Kalchuri",
        "rajasthani": "Kali Tithari",
        "tamil": "Karuppan Kuruvi",
        "iucn": "LC",
    },
    {
        "common": "Purple Sunbird",
        "scientific": "Cinnyris asiaticus",
        "hindi": "Shakar Khor",
        "rajasthani": "Phool Chuhi",
        "tamil": "Thean Kuruvi",
        "iucn": "LC",
    },
    {
        "common": "Asian Koel",
        "scientific": "Eudynamys scolopaceus",
        "hindi": "Koel",
        "rajasthani": "Koel",
        "tamil": "Kuil",
        "iucn": "LC",
    },
    {
        "common": "Coppersmith Barbet",
        "scientific": "Psilopogon haemacephala",
        "hindi": "Chota Hara Tota",
        "rajasthani": "Tamrathi",
        "tamil": "Vetti Kukku",
        "iucn": "LC",
    },
    {
        "common": "Common Myna",
        "scientific": "Acridotheres tristis",
        "hindi": "Desi Maina",
        "rajasthani": "Maina",
        "tamil": "Nattukuruvi",
        "iucn": "LC",
    },
    {
        "common": "Rose-ringed Parakeet",
        "scientific": "Psittacula krameri",
        "hindi": "Tota",
        "rajasthani": "Hiraman Tota",
        "tamil": "Killi",
        "iucn": "LC",
    },
    {
        "common": "Red-vented Bulbul",
        "scientific": "Pycnonotus cafer",
        "hindi": "Bulbul",
        "rajasthani": "Bulbul",
        "tamil": "Kondai Kuruvi",
        "iucn": "LC",
    },
    {
        "common": "House Sparrow",
        "scientific": "Passer domesticus",
        "hindi": "Gauraiya",
        "rajasthani": "Gauriya Chidiya",
        "tamil": "Veettu Kuruvi",
        "iucn": "LC",
    },
    {
        "common": "Black Kite",
        "scientific": "Milvus migrans",
        "hindi": "Cheel",
        "rajasthani": "Chil",
        "tamil": "Parundu",
        "iucn": "LC",
    },
    {
        "common": "Shikra",
        "scientific": "Accipiter badius",
        "hindi": "Shikra",
        "rajasthani": "Shikra",
        "tamil": "Sirukappu Pagal",
        "iucn": "LC",
    },
    {
        "common": "Brahminy Kite",
        "scientific": "Haliastur indus",
        "hindi": "Brahmini Cheel",
        "rajasthani": "Mota Cheel",
        "tamil": "Thirudan Parundu",
        "iucn": "LC",
    },
    {
        "common": "Black-shouldered Kite",
        "scientific": "Elanus caeruleus",
        "hindi": "Kapas Chidiya",
        "rajasthani": "—",
        "tamil": "Vella Parundu",
        "iucn": "LC",
    },
    {
        "common": "Indian Eagle Owl",
        "scientific": "Bubo bengalensis",
        "hindi": "Ghughu",
        "rajasthani": "Ullu",
        "tamil": "Andhai",
        "iucn": "LC",
    },
    {
        "common": "Spotted Owlet",
        "scientific": "Athene brama",
        "hindi": "Chugad",
        "rajasthani": "Khuddo",
        "tamil": "Pidi Andhai",
        "iucn": "LC",
    },
    {
        "common": "Barn Owl",
        "scientific": "Tyto alba",
        "hindi": "Safed Ullu",
        "rajasthani": "Safed Ullu",
        "tamil": "Barn Andhai",
        "iucn": "LC",
    },
    {
        "common": "Common Hoopoe",
        "scientific": "Upupa epops",
        "hindi": "Hudhud",
        "rajasthani": "Hudhud",
        "tamil": "Upupa",
        "iucn": "LC",
    },
    {
        "common": "Indian Roller",
        "scientific": "Coracias benghalensis",
        "hindi": "Nilkanth",
        "rajasthani": "Neelkanth",
        "tamil": "Peeranam Kuruvi",
        "iucn": "LC",
    },
    {
        "common": "White-throated Kingfisher",
        "scientific": "Halcyon smyrnensis",
        "hindi": "Kilkila",
        "rajasthani": "Kilkila",
        "tamil": "Vanaveli",
        "iucn": "LC",
    },
    {
        "common": "Common Kingfisher",
        "scientific": "Alcedo atthis",
        "hindi": "Chota Kilkila",
        "rajasthani": "Chhota Kilkila",
        "tamil": "Maen Vanaveli",
        "iucn": "LC",
    },
    {
        "common": "Pied Kingfisher",
        "scientific": "Ceryle rudis",
        "hindi": "Khandait",
        "rajasthani": "Khandaita",
        "tamil": "Varai Vanaveli",
        "iucn": "LC",
    },
    {
        "common": "Indian Peafowl",
        "scientific": "Pavo cristatus",
        "hindi": "Mor",
        "rajasthani": "Dhol Mor",
        "tamil": "Mayil",
        "iucn": "LC",
    },
    {
        "common": "Black Francolin",
        "scientific": "Francolinus francolinus",
        "hindi": "Kala Teetar",
        "rajasthani": "Kalo Titar",
        "tamil": "Karuppu Kadu Kozhi",
        "iucn": "LC",
    },
    {
        "common": "Grey Francolin",
        "scientific": "Ortygornis pondicerianus",
        "hindi": "Teetar",
        "rajasthani": "Titar",
        "tamil": "Kaadu Kozhi",
        "iucn": "LC",
    },
    {
        "common": "Yellow-footed Green Pigeon",
        "scientific": "Treron phoenicoptera",
        "hindi": "Hariyal",
        "rajasthani": "Hario",
        "tamil": "Pacchai Puraa",
        "iucn": "LC",
    },
    {
        "common": "Rock Pigeon",
        "scientific": "Columba livia",
        "hindi": "Kabutar",
        "rajasthani": "Kabutar",
        "tamil": "Puura",
        "iucn": "LC",
    },
    {
        "common": "Eurasian Collared Dove",
        "scientific": "Streptopelia decaocto",
        "hindi": "Dhol Fakhta",
        "rajasthani": "Peeli Ghughi",
        "tamil": "Tol Puraa",
        "iucn": "LC",
    },
    {
        "common": "Laughing Dove",
        "scientific": "Spilopelia senegalensis",
        "hindi": "Chhota Fakhta",
        "rajasthani": "Ghughi",
        "tamil": "Siriya Puraa",
        "iucn": "LC",
    },
    {
        "common": "Jungle Babbler",
        "scientific": "Argya striata",
        "hindi": "Saat Bhai",
        "rajasthani": "Jangli Saat Bhai",
        "tamil": "Kaadu Varigal",
        "iucn": "LC",
    },
    {
        "common": "Small Minivet",
        "scientific": "Pericrocotus cinnamomeus",
        "hindi": "Phatikia",
        "rajasthani": "Chhotki Phatki",
        "tamil": "Sirukappu Mini",
        "iucn": "LC",
    },
    {
        "common": "White-browed Wagtail",
        "scientific": "Motacilla maderaspatensis",
        "hindi": "Mamola",
        "rajasthani": "Mamola",
        "tamil": "Vella Puthai",
        "iucn": "LC",
    },
    {
        "common": "Common Tailorbird",
        "scientific": "Orthotomus sutorius",
        "hindi": "Phutki",
        "rajasthani": "Darji Chidiya",
        "tamil": "Thaiyalkaran",
        "iucn": "LC",
    },
    {
        "common": "Oriental Magpie Robin",
        "scientific": "Copsychus saularis",
        "hindi": "Dhayal",
        "rajasthani": "Dayal",
        "tamil": "Dhayar Kuruvi",
        "iucn": "LC",
    },
    {
        "common": "Ashy Prinia",
        "scientific": "Prinia socialis",
        "hindi": "Ashy Phutki",
        "rajasthani": "Bhoori Phutki",
        "tamil": "Saambal Kuruvi",
        "iucn": "LC",
    },
    {
        "common": "Plain Prinia",
        "scientific": "Prinia inornata",
        "hindi": "Saada Phutki",
        "rajasthani": "Saadi Phutki",
        "tamil": "Sada Kuruvi",
        "iucn": "LC",
    },
    {
        "common": "Indian Grey Hornbill",
        "scientific": "Ocyceros birostris",
        "hindi": "Dhanesh",
        "rajasthani": "Dhanesh",
        "tamil": "Irattai Mookkan",
        "iucn": "LC",
    },
    {
        "common": "Baya Weaver",
        "scientific": "Ploceus philippinus",
        "hindi": "Baya",
        "rajasthani": "Baya",
        "tamil": "Thoondi Kuruvi",
        "iucn": "LC",
    },
    {
        "common": "Indian Silverbill",
        "scientific": "Euodice malabarica",
        "hindi": "Lal Munia",
        "rajasthani": "Chanchri",
        "tamil": "Velli Munnai",
        "iucn": "LC",
    },
    {
        "common": "Red-wattled Lapwing",
        "scientific": "Vanellus indicus",
        "hindi": "Titihri",
        "rajasthani": "Teetihri",
        "tamil": "Aal Kaataan",
        "iucn": "LC",
    },
    {
        "common": "Black-winged Stilt",
        "scientific": "Himantopus himantopus",
        "hindi": "Teela Titar",
        "rajasthani": "Pankha Titar",
        "tamil": "Valavai Kuruvi",
        "iucn": "LC",
    },
    {
        "common": "Indian Pond Heron",
        "scientific": "Ardeola grayii",
        "hindi": "Andha Bagla",
        "rajasthani": "Andha Bagla",
        "tamil": "Kuruva Narai",
        "iucn": "LC",
    },
    {
        "common": "Little Egret",
        "scientific": "Egretta garzetta",
        "hindi": "Chhota Bagla",
        "rajasthani": "Chhoti Bagri",
        "tamil": "Siriya Narai",
        "iucn": "LC",
    },
    {
        "common": "Black-crowned Night Heron",
        "scientific": "Nycticorax nycticorax",
        "hindi": "Wak",
        "rajasthani": "Wak",
        "tamil": "Iravu Narai",
        "iucn": "LC",
    },
    {
        "common": "Paddyfield Pipit",
        "scientific": "Anthus rufulus",
        "hindi": "Chitta",
        "rajasthani": "Chitta",
        "tamil": "Vayalurai Kuruvi",
        "iucn": "LC",
    },
    # ── NT ──
    {
        "common": "Painted Stork",
        "scientific": "Mycteria leucocephala",
        "hindi": "Janghil",
        "rajasthani": "Janghil",
        "tamil": "Ponnarai",
        "iucn": "NT",
    },
    {
        "common": "Black-headed Ibis",
        "scientific": "Threskiornis melanocephalus",
        "hindi": "Safed Baza",
        "rajasthani": "Kali Tokh",
        "tamil": "Kattaan Kottan",
        "iucn": "NT",
    },
    {
        "common": "River Lapwing",
        "scientific": "Vanellus duvaucelii",
        "hindi": "Pathari",
        "rajasthani": "Nadi Titar",
        "tamil": "Aatrurai Kaataan",
        "iucn": "NT",
    },
    {
        "common": "Ferruginous Pochard",
        "scientific": "Aythya nyroca",
        "hindi": "Ferruginous Batakh",
        "rajasthani": "Lal Batakh",
        "tamil": "Irampu Vathu",
        "iucn": "NT",
    },
    # ── VU ──
    {
        "common": "Indian Spotted Eagle",
        "scientific": "Clanga hastata",
        "hindi": "Chotti Cheel",
        "rajasthani": "Chitti Kali Cheel",
        "tamil": "Pulli Erin",
        "iucn": "VU",
    },
    {
        "common": "Greater Spotted Eagle",
        "scientific": "Clanga clanga",
        "hindi": "Badi Cheel",
        "rajasthani": "Moti Kali Cheel",
        "tamil": "Pulli Erin",
        "iucn": "VU",
    },
    {
        "common": "Sarus Crane",
        "scientific": "Antigone antigone",
        "hindi": "Sarus",
        "rajasthani": "Sarus",
        "tamil": "Saaras Kurukku",
        "iucn": "VU",
    },
    {
        "common": "Bristled Grassbird",
        "scientific": "Schoenicola striatus",
        "hindi": "—",
        "rajasthani": "—",
        "tamil": "—",
        "iucn": "VU",
    },
    # ── EN ──
    {
        "common": "Egyptian Vulture",
        "scientific": "Neophron percnopterus",
        "hindi": "Safed Gidhh",
        "rajasthani": "Gotram",
        "tamil": "Vella Parunthu",
        "iucn": "EN",
    },
    {
        "common": "Lesser Florican",
        "scientific": "Sypheotides indicus",
        "hindi": "Kharmore",
        "rajasthani": "Lehan",
        "tamil": "—",
        "iucn": "EN",
    },
    # ── CR ──
    {
        "common": "White-rumped Vulture",
        "scientific": "Gyps bengalensis",
        "hindi": "Gidhh",
        "rajasthani": "Gidhh",
        "tamil": "Suryan Parunthu",
        "iucn": "CR",
    },
    {
        "common": "Indian Vulture",
        "scientific": "Gyps indicus",
        "hindi": "Desi Gidhh",
        "rajasthani": "Desi Gidhh",
        "tamil": "Nadu Parunthu",
        "iucn": "CR",
    },
    {
        "common": "Slender-billed Vulture",
        "scientific": "Gyps tenuirostris",
        "hindi": "Patli Chonch Gidhh",
        "rajasthani": "—",
        "tamil": "—",
        "iucn": "CR",
    },
    {
        "common": "Red-headed Vulture",
        "scientific": "Sarcogyps calvus",
        "hindi": "Raj Gidhh",
        "rajasthani": "Lal Matha Gidhh",
        "tamil": "Sivappu Parunthu",
        "iucn": "CR",
    },
    {
        "common": "Sociable Lapwing",
        "scientific": "Vanellus gregarius",
        "hindi": "—",
        "rajasthani": "—",
        "tamil": "—",
        "iucn": "CR",
    },
]


@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/mapping-dissonance")
def mapping_dissonance():
    entries = ArchiveEntry.query.order_by(ArchiveEntry.timestamp.desc()).all()
    care_counts = dict(
        db.session.query(CareSignature.entry_id, func.count(CareSignature.id))
        .group_by(CareSignature.entry_id)
        .all()
    )
    entries_data = [entry_to_dict(e, care_counts.get(e.id, 0)) for e in entries]

    folklore_entries = FolkloreEntry.query.order_by(FolkloreEntry.timestamp.desc()).all()
    witness_counts = dict(
        db.session.query(FolkloreWitness.entry_id, func.count(FolkloreWitness.id))
        .group_by(FolkloreWitness.entry_id)
        .all()
    )
    folklore_data = [folklore_to_dict(fe, witness_counts.get(fe.id, 0)) for fe in folklore_entries]

    return render_template(
        "archive.html",
        entries=entries,
        entries_json=entries_data,
        care_counts=care_counts,
        folklore_entries=folklore_entries,
        folklore_json=folklore_data,
        witness_counts=witness_counts,
    )


@app.route("/about")
def about():
    return render_template("about.html", birds=BIRDS)


@app.route("/bibliography")
def bibliography():
    return render_template("bibliography.html")


@app.route("/bird-list")
def bird_list():
    return render_template("bird_list.html", birds=BIRDS)


# ── CARE ROUTES ───────────────────────────────────────────────────────────────


@app.route("/care/sign/<int:entry_id>", methods=["POST"])
@(limiter.limit("30 per hour") if limiter else lambda f: f)
def care_sign(entry_id):
    if not current_user.is_authenticated:
        return jsonify({"status": "unauthenticated"}), 401

    ArchiveEntry.query.get_or_404(entry_id)

    existing = CareSignature.query.filter_by(
        entry_id=entry_id, user_id=current_user.id
    ).first()

    if existing:
        count = CareSignature.query.filter_by(entry_id=entry_id).count()
        return jsonify(
            {
                "status": "already_signed",
                "care_token": existing.care_token,
                "count": count,
            }
        )

    body = request.get_json(silent=True) or {}
    loc_verified = bool(body.get("location_verified", False))
    ts = datetime.now(timezone.utc).isoformat()

    # Build the note that goes on-chain
    note = (
        f"mapping-dissonance:"
        f"entry={entry_id}:user={current_user.id}:ts={ts}:"
        f"{CARE_STATEMENT}"
    )

    # Attempt Algorand Testnet notarisation; fall back to SHA-256 witness token
    txid = _algorand_notarise(note)
    if txid:
        token = txid
    else:
        token = hashlib.sha256(
            f"{entry_id}:{current_user.id}:{ts}".encode()
        ).hexdigest()

    sig = CareSignature(
        entry_id=entry_id,
        user_id=current_user.id,
        care_token=token,
        statement=CARE_STATEMENT,
        location_verified=loc_verified,
    )
    db.session.add(sig)
    db.session.flush()  # get sig.id before commit

    log = UserTransactionLog(
        user_id=current_user.id,
        care_signature_id=sig.id,
        txid=token,
    )
    db.session.add(log)
    db.session.commit()

    count = CareSignature.query.filter_by(entry_id=entry_id).count()
    return jsonify({"status": "signed", "care_token": token, "care_id": sig.id, "count": count})


@app.route("/care/status/<int:entry_id>")
def care_status(entry_id):
    count = CareSignature.query.filter_by(entry_id=entry_id).count()

    user_signed = False
    user_token = None
    if current_user.is_authenticated:
        sig = CareSignature.query.filter_by(
            entry_id=entry_id, user_id=current_user.id
        ).first()
        if sig:
            user_signed = True
            user_token = sig.care_token

    recent = (
        CareSignature.query.filter_by(entry_id=entry_id)
        .order_by(CareSignature.timestamp.desc())
        .limit(5)
        .all()
    )

    return jsonify(
        {
            "count": count,
            "user_signed": user_signed,
            "user_token": user_token,
            "recent": [
                {
                    "token": s.care_token[:20],
                    "timestamp": (
                        s.timestamp.strftime("%d %b %Y") if s.timestamp else ""
                    ),
                }
                for s in recent
            ],
        }
    )


@app.route("/ledger")
def ledger():
    rows = (
        db.session.query(CareSignature, ArchiveEntry, User)
        .join(ArchiveEntry, CareSignature.entry_id == ArchiveEntry.id)
        .join(User, CareSignature.user_id == User.id)
        .order_by(CareSignature.timestamp.desc())
        .limit(200)
        .all()
    )

    records = []
    for sig, entry, user in rows:
        user_hash = hashlib.sha256(user.email.encode()).hexdigest()[:16]
        records.append(
            {
                "timestamp": (
                    sig.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
                    if sig.timestamp
                    else "—"
                ),
                "user_hash": user_hash,
                "species": entry.species_name_merlin
                or entry.species_common
                or "Unknown",
                "location": entry.location_name or "—",
                "token": sig.care_token,
                "location_verified": sig.location_verified or False,
            }
        )

    return render_template("ledger.html", records=records)


# ── NEWS ──────────────────────────────────────────────────────────────────────

_news_cache: dict = {}
_NEWS_CACHE_TTL = 1800  # 30 minutes


def _cache_and_return(key, payload):
    now = time.time()
    expired = [k for k, v in _news_cache.items() if now - v["ts"] > _NEWS_CACHE_TTL]
    for k in expired:
        del _news_cache[k]
    _news_cache[key] = {"ts": now, "data": payload}
    return jsonify(payload)


def _fetch_news_payload(location):
    """Fetch (or return cached) news for one location. Returns a plain dict."""
    cache_key = location.strip().lower()
    cached = _news_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < _NEWS_CACHE_TTL:
        return cached["data"]

    kws = _loc_keywords(location) if location else []

    # TIER 1 — Curated Indian environmental RSS
    if kws:
        hits = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            futs = {
                ex.submit(_fetch_rss, name, url, kws): name for name, url in CURATED_RSS
            }
            for f in concurrent.futures.as_completed(futs):
                hits.extend(f.result())
        if hits:
            hits.sort(key=lambda x: x["_score"], reverse=True)
            for a in hits:
                a.pop("_score", None)
            seen = set()
            deduped = [a for a in hits if not (a["url"] in seen or seen.add(a["url"]))]
            payload = {"articles": deduped[:6], "_tier": 1}
            _cache_and_return(cache_key, payload)
            return payload

    # TIER 2 — GDELT (India-filtered, free)
    try:
        from gdeltdoc import GdeltDoc, Filters

        end = date.today()
        start = end - timedelta(days=180)
        kw = (
            f'"{location}" Delhi environment'
            if location
            else '"Delhi" environment ecology'
        )
        f = Filters(
            keyword=kw,
            country=["IN"],
            language="english",
            start_date=str(start),
            end_date=str(end),
            num_records=10,
        )
        df = GdeltDoc().article_search(f)
        if not df.empty:
            payload = {
                "articles": [
                    {
                        "title": r["title"],
                        "url": r["url"],
                        "source": {"name": r["domain"]},
                        "publishedAt": str(r["seendate"]),
                    }
                    for _, r in df.iterrows()
                ][:6],
                "_tier": 2,
            }
            _cache_and_return(cache_key, payload)
            return payload
    except Exception:
        pass

    # TIER 3 — Mediastack, India-locked
    try:
        ms_key = os.environ.get("MEDIASTACK_API_KEY", "")
        if not ms_key:
            raise ValueError("No mediastack key")
        env_kws = (
            "environment,habitat loss,urbanization,pollution,construction,"
            "conservation,biodiversity,wildlife,deforestation,green cover,"
            "encroachment,land use"
        )
        keywords = f"Delhi,{location},{env_kws}" if location else f"Delhi,{env_kws}"
        resp = requests.get(
            "http://api.mediastack.com/v1/news",
            params={
                "access_key": ms_key,
                "keywords": keywords,
                "countries": "in",
                "languages": "en",
                "limit": 6,
                "sort": "published_desc",
            },
            timeout=10,
        )
        resp.raise_for_status()
        ms_data = resp.json()
        articles = [
            {
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "source": {"name": a.get("source", "")},
                "publishedAt": a.get("published_at", ""),
            }
            for a in ms_data.get("data", [])
            if a.get("url")
        ]
        payload = {"articles": articles[:6], "_tier": 3}
        _cache_and_return(cache_key, payload)
        return payload
    except Exception:
        payload = {"articles": [], "_tier": 0}
        _cache_and_return(cache_key, payload)
        return payload


@app.route("/get_context_news")
def get_context_news():
    location = request.args.get("location", "")
    return jsonify(_fetch_news_payload(location))


@app.route("/get_context_news_batch", methods=["POST"])
@(csrf.exempt if csrf else lambda f: f)
def get_context_news_batch():
    """Fetch news for multiple locations in one round-trip (used by archive page)."""
    body = request.get_json(silent=True) or {}
    locations = body.get("locations", [])
    if not locations or not isinstance(locations, list):
        return jsonify({})
    unique = list(dict.fromkeys(str(l).strip() for l in locations if l))[:20]
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(_fetch_news_payload, loc): loc for loc in unique}
        for f in concurrent.futures.as_completed(futs):
            loc = futs[f]
            try:
                results[loc] = f.result()
            except Exception:
                results[loc] = {"articles": []}
    return jsonify(results)


@app.route("/get_iucn_status/<sci_name>")
def get_iucn_status(sci_name):
    token = os.environ.get("IUCN_TOKEN", "")
    try:
        url = f"https://api.iucnredlist.org/api/v4/taxa/scientific_name/{sci_name.replace(' ', '%20')}"
        res = requests.get(url, headers={"Authorization": f"Bearer {token}"}).json()
        if "assessments" in res and res["assessments"]:
            return jsonify(
                {"status": res["assessments"][0]["red_list_category"]["name"]}
            )
    except Exception:
        pass
    return jsonify({"status": "Data pending"})


@app.route("/upload", methods=["POST"])
@login_required
@(limiter.limit("10 per hour") if limiter else lambda f: f)
def upload():
    file = request.files.get("file")
    if not file:
        return redirect(url_for("mapping_dissonance"))

    loc_name = request.form.get("location_name", "Delhi").strip()
    merlin_name = request.form.get("species_name_merlin", "").strip()
    time_of_day = request.form.get("recording_time_of_day", "").strip()
    rec_date_str = request.form.get("recording_date", "").strip()
    rec_date = date.fromisoformat(rec_date_str) if rec_date_str else None

    filename = (
        f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{secure_filename(file.filename)}"
    )
    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(path)

    # Resolve coords first — BirdNET uses location to narrow candidate species
    lat, lng = get_coords(loc_name)

    global analyzer
    if BIRDNET_AVAILABLE and analyzer is None:
        try:
            analyzer = Analyzer()
        except Exception as e:
            logger.warning("BirdNET Analyzer unavailable: %s", e)

    common, sci, conf = merlin_name or "Unknown", "Unknown", 0.0
    if analyzer:
        try:
            rec_date_dt = datetime(rec_date.year, rec_date.month, rec_date.day) if rec_date else datetime.now()
            # First pass: location-filtered (faster, region-aware)
            rec = Recording(
                analyzer,
                path,
                lat=lat,
                lon=lng,
                date=rec_date_dt,
                min_conf=0.1,
            )
            rec.analyze()
            if rec.detections:
                common = rec.detections[0]["common_name"]
                sci = rec.detections[0]["scientific_name"]
                conf = rec.detections[0]["confidence"]
            else:
                # Second pass: global species list — reset location filter state first
                analyzer.custom_species_list = []
                analyzer.has_custom_species_list = False
                rec2 = Recording(analyzer, path, min_conf=0.1)
                rec2.analyze()
                if rec2.detections:
                    common = rec2.detections[0]["common_name"]
                    sci = rec2.detections[0]["scientific_name"]
                    conf = rec2.detections[0]["confidence"]
        except Exception as e:
            logger.warning("BirdNET analysis failed: %s", e)

    # Reject unidentified sounds — delete the saved file and abort
    if common == "Unknown":
        try:
            os.remove(path)
        except OSError:
            pass
        return redirect(url_for("mapping_dissonance"))

    # IUCN lookup — static dict first (common name → code), then scientific name
    iucn_status = _BIRD_IUCN.get(common) or _SCI_IUCN.get(sci)
    if not iucn_status and sci and sci != "Unknown":
        try:
            token = os.environ.get("IUCN_TOKEN", "")
            url = f"https://api.iucnredlist.org/api/v4/taxa/scientific_name/{sci.replace(' ', '%20')}"
            res = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=5).json()
            if res.get("assessments"):
                raw = res["assessments"][0]["red_list_category"].get("code", "")
                iucn_status = raw if raw in ("LC", "NT", "VU", "EN", "CR", "DD", "EW", "EX") else None
        except Exception:
            pass
    iucn_status = iucn_status or "LC"

    last = ArchiveEntry.query.order_by(ArchiveEntry.id.desc()).first()
    prev_h = last.current_hash if last else "0" * 64
    new_h = hashlib.sha256(f"{prev_h}{common}{filename}".encode()).hexdigest()

    db.session.add(
        ArchiveEntry(
            species_common=common,
            species_sci=sci,
            species_name_merlin=merlin_name,
            confidence=conf,
            location_name=loc_name,
            lat=lat,
            lng=lng,
            recording_time_of_day=time_of_day,
            recording_date=rec_date,
            file_path=filename,
            prev_hash=prev_h,
            current_hash=new_h,
            iucn_status=iucn_status,
        )
    )
    db.session.commit()
    return redirect(url_for("mapping_dissonance"))


@app.route("/audio/<filename>")
def serve_audio(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/folklore/submit", methods=["POST"])
@login_required
@(limiter.limit("5 per hour") if limiter else lambda f: f)
def folklore_submit():
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    loc_name = request.form.get("location_name", "").strip()

    # File upload takes priority over textarea if non-empty
    uploaded = request.files.get("file")
    if uploaded and uploaded.filename:
        ext = os.path.splitext(secure_filename(uploaded.filename))[1].lower()
        if ext == ".txt":
            try:
                file_text = uploaded.read().decode("utf-8", errors="replace").strip()
                if file_text:
                    body = file_text
            except Exception:
                pass

    if not title or not body:
        return redirect(url_for("mapping_dissonance"))

    lat, lng = get_coords(loc_name) if loc_name else (28.6139, 77.2090)

    last = FolkloreEntry.query.order_by(FolkloreEntry.id.desc()).first()
    prev_h = last.current_hash if last else "0" * 64
    new_h = hashlib.sha256(f"{prev_h}{title}{current_user.id}".encode()).hexdigest()

    fe = FolkloreEntry(
        title=title,
        body=body,
        location_name=loc_name,
        lat=lat,
        lng=lng,
        user_id=current_user.id,
        prev_hash=prev_h,
        current_hash=new_h,
    )
    db.session.add(fe)
    db.session.commit()
    return redirect(url_for("mapping_dissonance") + "#stories")


@app.route("/folklore/witness/<int:entry_id>", methods=["POST"])
def folklore_witness(entry_id):
    if not current_user.is_authenticated:
        return jsonify({"status": "unauthenticated"}), 401

    FolkloreEntry.query.get_or_404(entry_id)

    existing = FolkloreWitness.query.filter_by(
        entry_id=entry_id, user_id=current_user.id
    ).first()
    if existing:
        count = FolkloreWitness.query.filter_by(entry_id=entry_id).count()
        return jsonify({"status": "already_witnessed", "count": count})

    ts = datetime.now(timezone.utc).isoformat()
    note = f"mapping-dissonance:folklore={entry_id}:user={current_user.id}:ts={ts}"
    txid = _algorand_notarise(note)
    token = txid if txid else hashlib.sha256(
        f"{entry_id}:{current_user.id}:{ts}".encode()
    ).hexdigest()

    wit = FolkloreWitness(entry_id=entry_id, user_id=current_user.id, token=token)
    db.session.add(wit)
    db.session.flush()
    db.session.commit()

    count = FolkloreWitness.query.filter_by(entry_id=entry_id).count()
    return jsonify({"status": "witnessed", "token": token, "witness_id": wit.id, "count": count})


@app.route("/login", methods=["GET", "POST"])
@(limiter.limit("20 per hour") if limiter else lambda f: f)
def login():
    if request.method == "POST":
        user = User.query.filter_by(email=request.form["email"].strip().lower()).first()
        if user and check_password_hash(user.password, request.form["password"]):
            login_user(user, remember=True)
            return redirect(url_for("mapping_dissonance"))
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
@(limiter.limit("5 per hour") if limiter else lambda f: f)
def register():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        hashed_pw = generate_password_hash(
            request.form["password"], method="pbkdf2:sha256"
        )
        db.session.add(User(email=email, password=hashed_pw))
        db.session.commit()
        return redirect(url_for("login"))
    return render_template("register.html")


_BIRD_IUCN = {b["common"]: b["iucn"] for b in BIRDS}
_SCI_IUCN  = {b["scientific"]: b["iucn"] for b in BIRDS}


@app.route("/api/landing-records")
def landing_records():
    entries = (
        ArchiveEntry.query.filter(
            ArchiveEntry.lat.isnot(None), ArchiveEntry.lng.isnot(None)
        )
        .order_by(ArchiveEntry.timestamp.desc())
        .limit(200)
        .all()
    )

    care_counts = dict(
        db.session.query(CareSignature.entry_id, func.count(CareSignature.id))
        .group_by(CareSignature.entry_id)
        .all()
    )

    records = []
    for e in entries:
        records.append(
            {
                "id": e.id,
                "lat": e.lat,
                "lng": e.lng,
                "species_common": e.species_common or "Unknown",
                "location_name": e.location_name or "Field Recording",
                "timestamp": (
                    e.timestamp.strftime("%d %b %Y, %H:%M") if e.timestamp else ""
                ),
                "file_path": e.file_path,
                "care_count": care_counts.get(e.id, 0),
                "iucn": _BIRD_IUCN.get(e.species_common, "LC"),
            }
        )

    return jsonify(records)


@app.route("/api/folklore-pins")
def api_folklore_pins():
    entries = (
        FolkloreEntry.query
        .filter(FolkloreEntry.lat.isnot(None), FolkloreEntry.lng.isnot(None))
        .order_by(FolkloreEntry.timestamp.desc())
        .limit(200)
        .all()
    )
    return jsonify([{
        "id": e.id,
        "lat": e.lat,
        "lng": e.lng,
        "title": e.title,
        "body": e.body or "",
        "location_name": e.location_name or "",
        "timestamp": e.timestamp.strftime("%d %b %Y") if e.timestamp else "",
    } for e in entries])


@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("landing"))


# Ensure tables exist whether running via gunicorn or directly
with app.app_context():
    db.create_all()
    # Add indexes to existing DBs that predate index=True on the models
    with db.engine.connect() as _conn:
        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_archive_entry_timestamp ON archive_entry(timestamp)"))
        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_care_signature_entry_id ON care_signature(entry_id)"))
        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_care_signature_user_id ON care_signature(user_id)"))
        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_transaction_log_user_id ON user_transaction_log(user_id)"))
        _conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_transaction_log_care_sig ON user_transaction_log(care_signature_id)"))
        # Migrate existing DBs that predate the iucn_status column
        try:
            _conn.execute(text("ALTER TABLE archive_entry ADD COLUMN iucn_status VARCHAR(10) DEFAULT 'LC'"))
        except Exception:
            pass  # column already exists
        _conn.commit()

    # Purge existing entries that BirdNET never identified
    unknown_entries = ArchiveEntry.query.filter(
        (ArchiveEntry.species_common == "Unknown") | (ArchiveEntry.species_common == None)
    ).all()
    _evict_locs = {e.location_name.strip().lower() for e in unknown_entries if e.location_name}
    for _e in unknown_entries:
        if _e.file_path:
            _fp = os.path.join(app.config["UPLOAD_FOLDER"], _e.file_path)
            try:
                os.remove(_fp)
            except OSError:
                pass
        db.session.delete(_e)
    if unknown_entries:
        db.session.commit()
        # Evict news cache entries for affected locations
        for _loc in _evict_locs:
            _news_cache.pop(_loc, None)

if __name__ == "__main__":
    app.run(debug=False, use_reloader=False, port=5001)
