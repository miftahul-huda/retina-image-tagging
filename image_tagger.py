#!/usr/bin/env python3
"""
image_tagger.py
---------------
Aplikasi untuk menambahkan informasi metadata ke gambar-gambar yang
tersimpan di Google Cloud Storage. Data gambar diambil dari PostgreSQL.

Penggunaan lokal / Compute Engine:
    python3 image_tagger.py --start-date 2025-06-01 --end-date 2025-06-30

Cloud Run Jobs (paralel):
    Jalankan Job dengan --tasks N. Setiap task secara otomatis membaca
    env var CLOUD_RUN_TASK_INDEX dan CLOUD_RUN_TASK_COUNT yang di-inject
    oleh Cloud Run, lalu memproses hanya subset record miliknya.
"""

import argparse
import io
import os
import sys
import traceback
import time
from datetime import datetime

import psycopg2
import psycopg2.extras
from google.cloud import storage
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

# Muat environment variables dari file .env (jika ada)
load_dotenv()

# ─────────────────────────────────────────────────────────────────────
# KONFIGURASI DATABASE
# ─────────────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "34.50.82.149"),
    "dbname":   os.getenv("DB_NAME", "retail-intelligence"),
    "user":     os.getenv("DB_USER", "nodeuser"),
    "password": os.getenv("DB_PASS", "rotikeju98"),
    "port":     int(os.getenv("DB_PORT", "5432")),
    "connect_timeout": 15,
}

# ─────────────────────────────────────────────────────────────────────
# KONFIGURASI GCS
# ─────────────────────────────────────────────────────────────────────
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "retail-intelligence-bucket")

# ─────────────────────────────────────────────────────────────────────
# KONFIGURASI PANEL INFORMASI
# ─────────────────────────────────────────────────────────────────────
PANEL_HEIGHT      = 350          # tinggi panel putih (px)
PANEL_BG_COLOR    = (255, 255, 255)   # warna latar panel
TEXT_COLOR        = (30, 30, 30)      # warna teks
ACCENT_COLOR      = (0, 102, 204)     # warna aksen (judul baris)
PADDING_X         = 30           # margin kiri teks
PADDING_Y         = 30           # margin atas teks
LINE_SPACING      = 75           # jarak antar baris (px)
FONT_SIZE_LABEL   = 26
FONT_SIZE_VALUE   = 26

# Prioritas font: DejaVuSans (Linux/CE) → Arial (macOS) → default PIL
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
]

FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
]


def _load_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def get_db_connection():
    """Buat koneksi PostgreSQL."""
    return psycopg2.connect(**DB_CONFIG)


def fetch_records(conn, start_date: str, end_date: str) -> list[dict]:
    """
    Ambil data dari tabel uploadfile JOIN store dengan filter tanggal
    pada kolom createdAt. Hanya ambil record yang punya gambar.

    Ada beberapa sumber gambar:
    - Record biasa: gambar diambil dari uploadfile.uploaded_filename dan
      hasilnya disimpan kembali ke uploadfile.uploaded_filename_withinfo.
    - Record dengan imageCategory = 'starter-package': gambar diambil dari
      starterpackage.photoUrl (via starterpackage.upload_file_id) dan
      hasilnya disimpan ke starterpackage.photoUrlWithInfo.
    - Record dengan imageCategory = 'voucherfisik': gambar diambil dari
      "voucherPackage".photoUrl (via "voucherPackage".upload_file_id) dan
      hasilnya disimpan ke "voucherPackage".photoUrlWithInfo.
    Satu record uploadfile bisa punya beberapa foto starterpackage/voucherPackage.
    """
    query = """
        SELECT
            u.id,
            u.store_id,
            u."createdAt",
            u.uploaded_by_email,
            u.uploaded_by_fullname,
            u.uploaded_filename AS source_path,
            s.storeid,
            s.store_name,
            s.store_city,
            s.store_area,
            s.store_branch,
            s.store_region,
            'uploadfile' AS record_type,
            u.id AS update_id
        FROM uploadfile u
        JOIN store s ON s.storeid = u.store_id
        WHERE
            u."createdAt" >= %s::date
            AND u."createdAt" < (%s::date + INTERVAL '1 day')
            AND u.uploaded_filename IS NOT NULL
            AND u.uploaded_filename LIKE 'gs://%%'
            AND u.uploaded_filename_withinfo IS NULL
            AND u."imageCategory" IS DISTINCT FROM 'starter-package'
            AND u."imageCategory" IS DISTINCT FROM 'voucherfisik'

        UNION ALL

        SELECT
            u.id,
            u.store_id,
            u."createdAt",
            u.uploaded_by_email,
            u.uploaded_by_fullname,
            sp."photoUrl" AS source_path,
            s.storeid,
            s.store_name,
            s.store_city,
            s.store_area,
            s.store_branch,
            s.store_region,
            'starterpackage' AS record_type,
            sp.id AS update_id
        FROM starterpackage sp
        JOIN uploadfile u ON u.id = sp.upload_file_id
        JOIN store s ON s.storeid = u.store_id
        WHERE
            u."createdAt" >= %s::date
            AND u."createdAt" < (%s::date + INTERVAL '1 day')
            AND u."imageCategory" = 'starter-package'
            AND sp."photoUrl" IS NOT NULL
            AND sp."photoUrl" LIKE 'gs://%%'
            AND sp."photoUrlWithInfo" IS NULL

        UNION ALL

        SELECT
            u.id,
            u.store_id,
            u."createdAt",
            u.uploaded_by_email,
            u.uploaded_by_fullname,
            vp."photoUrl" AS source_path,
            s.storeid,
            s.store_name,
            s.store_city,
            s.store_area,
            s.store_branch,
            s.store_region,
            'voucherPackage' AS record_type,
            vp.id AS update_id
        FROM "voucherPackage" vp
        JOIN uploadfile u ON u.id = vp.upload_file_id
        JOIN store s ON s.storeid = u.store_id
        WHERE
            u."createdAt" >= %s::date
            AND u."createdAt" < (%s::date + INTERVAL '1 day')
            AND u."imageCategory" = 'voucherfisik'
            AND vp."photoUrl" IS NOT NULL
            AND vp."photoUrl" LIKE 'gs://%%'
            AND vp."photoUrlWithInfo" IS NULL

        ORDER BY "createdAt" ASC
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            query,
            (start_date, end_date, start_date, end_date, start_date, end_date),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def fetch_record_by_id(conn, record_id: int) -> list[dict]:
    """
    Ambil record untuk satu uploadfile ID.

    Jika imageCategory record tersebut 'starter-package' atau 'voucherfisik',
    kembalikan setiap foto starterpackage/voucherPackage yang terkait (bisa
    lebih dari satu) alih-alih uploaded_filename milik uploadfile.
    """
    query = """
        SELECT
            u.id,
            u.store_id,
            u."createdAt",
            u.uploaded_by_email,
            u.uploaded_by_fullname,
            u.uploaded_filename AS source_path,
            s.store_name,
            s.store_city,
            s.store_area,
            s.store_branch,
            s.store_region,
            'uploadfile' AS record_type,
            u.id AS update_id
        FROM uploadfile u
        JOIN store s ON s.storeid = u.store_id
        WHERE u.id = %s
            AND u."imageCategory" IS DISTINCT FROM 'starter-package'
            AND u."imageCategory" IS DISTINCT FROM 'voucherfisik'

        UNION ALL

        SELECT
            u.id,
            u.store_id,
            u."createdAt",
            u.uploaded_by_email,
            u.uploaded_by_fullname,
            sp."photoUrl" AS source_path,
            s.store_name,
            s.store_city,
            s.store_area,
            s.store_branch,
            s.store_region,
            'starterpackage' AS record_type,
            sp.id AS update_id
        FROM starterpackage sp
        JOIN uploadfile u ON u.id = sp.upload_file_id
        JOIN store s ON s.storeid = u.store_id
        WHERE u.id = %s AND u."imageCategory" = 'starter-package'

        UNION ALL

        SELECT
            u.id,
            u.store_id,
            u."createdAt",
            u.uploaded_by_email,
            u.uploaded_by_fullname,
            vp."photoUrl" AS source_path,
            s.store_name,
            s.store_city,
            s.store_area,
            s.store_branch,
            s.store_region,
            'voucherPackage' AS record_type,
            vp.id AS update_id
        FROM "voucherPackage" vp
        JOIN uploadfile u ON u.id = vp.upload_file_id
        JOIN store s ON s.storeid = u.store_id
        WHERE u.id = %s AND u."imageCategory" = 'voucherfisik'
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, (record_id, record_id, record_id))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def bulk_update_processed_records(conn, ids_list: list[int]):
    """
    Update massal kolom uploaded_filename_withinfo menggunakan regex berdasarkan list ID.
    """
    if not ids_list:
        return 0

    query = """
        UPDATE uploadfile
        SET uploaded_filename_withinfo = regexp_replace(uploaded_filename, '(\\.[a-zA-Z0-9]+)$', '_with_info\\1')
        WHERE id = ANY(%s)
    """
    print(f"\n→ Memperbarui database secara massal ({len(ids_list)} record uploadfile) ...")
    try:
        with conn.cursor() as cur:
            cur.execute(query, (ids_list,))
            updated_count = cur.rowcount
        conn.commit()
        print(f"  ✓ Berhasil memperbarui {updated_count} record di database.")
        return updated_count
    except Exception as e:
        print(f"  ✗ Gagal melakukan bulk update: {e}")
        conn.rollback()
        return 0


def bulk_update_processed_starterpackage(conn, ids_list: list[int]):
    """
    Update massal kolom starterpackage.photoUrlWithInfo menggunakan regex
    berdasarkan list starterpackage.id.
    """
    if not ids_list:
        return 0

    query = """
        UPDATE starterpackage
        SET "photoUrlWithInfo" = regexp_replace("photoUrl", '(\\.[a-zA-Z0-9]+)$', '_with_info\\1')
        WHERE id = ANY(%s)
    """
    print(f"\n→ Memperbarui database secara massal ({len(ids_list)} record starterpackage) ...")
    try:
        with conn.cursor() as cur:
            cur.execute(query, (ids_list,))
            updated_count = cur.rowcount
        conn.commit()
        print(f"  ✓ Berhasil memperbarui {updated_count} record di database.")
        return updated_count
    except Exception as e:
        print(f"  ✗ Gagal melakukan bulk update: {e}")
        conn.rollback()
        return 0


def bulk_update_processed_voucherpackage(conn, ids_list: list[int]):
    """
    Update massal kolom "voucherPackage".photoUrlWithInfo menggunakan regex
    berdasarkan list "voucherPackage".id.
    """
    if not ids_list:
        return 0

    query = """
        UPDATE "voucherPackage"
        SET "photoUrlWithInfo" = regexp_replace("photoUrl", '(\\.[a-zA-Z0-9]+)$', '_with_info\\1')
        WHERE id = ANY(%s)
    """
    print(f"\n→ Memperbarui database secara massal ({len(ids_list)} record voucherPackage) ...")
    try:
        with conn.cursor() as cur:
            cur.execute(query, (ids_list,))
            updated_count = cur.rowcount
        conn.commit()
        print(f"  ✓ Berhasil memperbarui {updated_count} record di database.")
        return updated_count
    except Exception as e:
        print(f"  ✗ Gagal melakukan bulk update: {e}")
        conn.rollback()
        return 0


def parse_gcs_path(gcs_path: str) -> tuple[str, str]:
    """
    Pisahkan gs://bucket-name/path/to/file.jpg
    menjadi (bucket_name, blob_name).
    """
    if not gcs_path.startswith("gs://"):
        raise ValueError(f"Bukan GCS path valid: {gcs_path}")
    without_scheme = gcs_path[len("gs://"):]
    bucket_name, _, blob_name = without_scheme.partition("/")
    return bucket_name, blob_name


def build_output_blob_name(blob_name: str) -> str:
    """
    Ubah path blob menjadi path output dengan suffix '_with_info'.
    Contoh: prod-uploads/poster/foo.jpg → prod-uploads/poster/foo_with_info.jpg
    """
    root, ext = os.path.splitext(blob_name)
    return f"{root}_with_info{ext}"


def download_image_from_gcs(storage_client: storage.Client, bucket_name: str, blob_name: str) -> bytes:
    """Unduh blob dari GCS dan kembalikan sebagai bytes."""
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    return blob.download_as_bytes()


def upload_image_to_gcs(
    storage_client: storage.Client,
    bucket_name: str,
    blob_name: str,
    image_bytes: bytes,
    content_type: str = "image/jpeg",
) -> str:
    """Unggah bytes gambar ke GCS dan kembalikan gs:// path."""
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(image_bytes, content_type=content_type)
    return f"gs://{bucket_name}/{blob_name}"


def format_created_at(value) -> str:
    """Format datetime/date ke string yang mudah dibaca."""
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%d %B %Y, %H:%M WIB")
    try:
        dt = datetime.fromisoformat(str(value))
        return dt.strftime("%d %B %Y, %H:%M WIB")
    except Exception:
        return str(value)


def add_info_panel(image_bytes: bytes, record: dict, source_format: str) -> bytes:
    """
    Tambahkan panel putih berisi informasi di bagian bawah gambar.
    Kembalikan gambar hasil modifikasi sebagai bytes.
    """
    # Buka gambar asli
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    orig_w, orig_h = img.size

    # Buat kanvas baru: lebar sama, tinggi = original + panel
    new_h = orig_h + PANEL_HEIGHT
    canvas = Image.new("RGB", (orig_w, new_h), PANEL_BG_COLOR)

    # Tempel gambar asli di atas
    canvas.paste(img, (0, 0))

    # Gambar garis pemisah tipis antara gambar dan panel
    draw = ImageDraw.Draw(canvas)
    separator_y = orig_h
    draw.rectangle(
        [(0, separator_y), (orig_w, separator_y + 3)],
        fill=(0, 102, 204),
    )

    # Muat font
    font_label = _load_font(FONT_BOLD_CANDIDATES, FONT_SIZE_LABEL)
    font_value = _load_font(FONT_CANDIDATES, FONT_SIZE_VALUE)

    # Susun baris informasi
    created_str   = format_created_at(record.get("createdAt"))
    uploader_str  = "{} ({})".format(
        record.get("uploaded_by_fullname") or "-",
        record.get("uploaded_by_email") or "-",
    )
    store_str     = "{} ({})".format(
        record.get("store_name") or "-",
        record.get("storeid") or "-",
    )
    location_str  = "{}, {}, {}, {}".format(
        record.get("store_city") or "-",
        record.get("store_region") or "-",
        record.get("store_branch") or "-",
        record.get("store_area") or "-",
    )

    lines = [
        ("Upload Date   :", created_str),
        ("By :", uploader_str),
        ("Outlet :", store_str),
        ("Location :", location_str),
    ]

    # Gambar teks pada panel putih
    text_y = orig_h + 10 + PADDING_Y
    col_label_x = PADDING_X
    col_value_x = PADDING_X + 220   # offset kolom nilai

    for label, value in lines:
        draw.text((col_label_x, text_y), label, font=font_label, fill=ACCENT_COLOR)
        draw.text((col_value_x, text_y), value, font=font_value, fill=TEXT_COLOR)
        text_y += LINE_SPACING

    # Simpan ke buffer
    out_buf = io.BytesIO()
    save_format = source_format.upper()
    if save_format == "JPG":
        save_format = "JPEG"
    if save_format not in ("JPEG", "PNG", "WEBP", "BMP", "TIFF"):
        save_format = "JPEG"

    save_kwargs: dict = {}
    if save_format == "JPEG":
        save_kwargs["quality"] = 92
        save_kwargs["subsampling"] = 0

    canvas.save(out_buf, format=save_format, **save_kwargs)
    return out_buf.getvalue()


def get_content_type(ext: str) -> str:
    """Kembalikan MIME type berdasarkan ekstensi file."""
    mapping = {
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png":  "image/png",
        ".webp": "image/webp",
        ".bmp":  "image/bmp",
        ".tiff": "image/tiff",
        ".tif":  "image/tiff",
    }
    return mapping.get(ext.lower(), "image/jpeg")


def print_separator(char: str = "─", width: int = 70):
    print(char * width)


def get_task_info() -> tuple[int, int]:
    """
    Baca informasi task dari environment variable Cloud Run Jobs.

    Cloud Run Jobs secara otomatis mengisi:
      CLOUD_RUN_TASK_INDEX  – indeks task saat ini (0-based)
      CLOUD_RUN_TASK_COUNT  – total jumlah task paralel

    Jika env var tidak ada (local / Compute Engine), kembalikan (0, 1)
    sehingga semua record diproses oleh satu "task".
    """
    try:
        task_index = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
        task_count = int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1"))
        if task_count < 1:
            task_count = 1
        if task_index < 0 or task_index >= task_count:
            task_index = 0
        return task_index, task_count
    except ValueError:
        return 0, 1


def split_records_for_task(records: list[dict], task_index: int, task_count: int) -> list[dict]:
    """
    Bagi records ke task menggunakan slice modulo.
    Task i memproses records[i], records[i+N], records[i+2N], ...
    Ini memastikan pembagian merata tanpa perlu sinkronisasi antar task.
    """
    return records[task_index::task_count]


def process_records(
    records: list[dict],
    storage_client: storage.Client,
    processed_ids: dict,
    task_index: int = 0,
    task_count: int = 1,
    total_all_records: int = 0,
) -> tuple[int, int, int]:
    """
    Proses records yang sudah dibagi untuk task ini:
    - Unduh gambar dari GCS
    - Tambahkan panel informasi
    - Unggah kembali dengan nama _with_info
    """
    total   = len(records)
    success = 0
    failed  = 0
    skipped = 0
    end_idx = (task_index + 1) * total

    print_separator()
    if task_count > 1:
        print(f"  Task {task_index + 1} dari {task_count} | Gambar dalam task ini: {total}")
    else:
        print(f"  Total gambar yang akan diproses: {total}")
    print_separator()

    for idx, record in enumerate(records, start=1):
        gcs_path   = record.get("source_path", "")
        record_type = record.get("record_type", "uploadfile")
        store_name = "{} ({})".format(
            record.get("store_name") or "-",
            record.get("storeid") or "-",
        )
        created_at = format_created_at(record.get("createdAt"))
        # Tampilkan informasi task jika dalam mode multitask

        task_record_idx = idx + (task_index * total)
        prefix = f"[Task {task_index}]" if task_count > 1 else ""
        print(f"\n{prefix}[{task_record_idx} of {end_idx} from {total_all_records}] Memproses ({record_type}) ...")
        print(f"  File      : {gcs_path}")
        print(f"  Outlet      : {store_name}")
        print(f"  Tanggal   : {created_at}")

        try:
            bucket_name, blob_name = parse_gcs_path(gcs_path)
            output_blob_name = build_output_blob_name(blob_name)

            # Unduh gambar
            print(f"  ↓  Mengunduh dari GCS ...")
            image_bytes = download_image_from_gcs(storage_client, bucket_name, blob_name)

            # Dapatkan ekstensi
            _, ext = os.path.splitext(blob_name)
            fmt = ext.lstrip(".")

            # Tambahkan panel informasi
            print(f"  ✏  Menambahkan panel informasi ...")
            modified_bytes = add_info_panel(image_bytes, record, fmt)

            # Unggah ke GCS
            output_gcs_path = f"gs://{bucket_name}/{output_blob_name}"
            content_type    = get_content_type(ext)
            print(f"  ↑  Mengunggah ke GCS: {output_gcs_path}")
            upload_image_to_gcs(
                storage_client, bucket_name, output_blob_name,
                modified_bytes, content_type,
            )

            print(f"  ✓  Selesai → {output_gcs_path}")
            processed_ids.setdefault(record_type, []).append(record["update_id"])
            success += 1

        except Exception as e:
            print(f"  ✗  GAGAL: {e}")
            traceback.print_exc()
            failed += 1

    # Ringkasan per task
    print()
    print_separator("═")
    task_label = f" TASK {task_index}" if task_count > 1 else ""
    print(f"  RINGKASAN{task_label}")
    print(f"  Total   : {total}")
    print(f"  Berhasil: {success}")
    print(f"  Dilewati: {skipped}")
    print(f"  Gagal   : {failed}")
    print_separator("═")

    return success, failed, skipped


def parse_args():
    parser = argparse.ArgumentParser(
        description="Tambahkan informasi metadata ke gambar di GCS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Contoh:
    
  python3 image_tagger.py --start-date 2025-01-01 --end-date 2025-12-31
        """,
    )
    parser.add_argument(
        "--start-date",
        required=True,
        help="Tanggal awal filter createdAt (format: YYYY-MM-DD)",
        metavar="YYYY-MM-DD",
    )
    parser.add_argument(
        "--end-date",
        required=True,
        help="Tanggal akhir filter createdAt (format: YYYY-MM-DD, inklusif)",
        metavar="YYYY-MM-DD",
    )
    return parser.parse_args()


def validate_date(date_str: str, label: str) -> datetime:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print(f"ERROR: Format {label} tidak valid '{date_str}'. Gunakan format YYYY-MM-DD.")
        sys.exit(1)


def run_tagging_process(start_date_str: str, end_date_str: str):
    """
    Fungsi utama yang bisa dipanggil dari API atau CLI.
    """
    start_dt = validate_date(start_date_str, "--start-date")
    end_dt   = validate_date(end_date_str,   "--end-date")

    if start_dt > end_dt:
        print("ERROR: --start-date tidak boleh lebih besar dari --end-date.")
        return {"status": "error", "message": "start_date > end_date"}

    # Baca info task Cloud Run Jobs (jika tidak ada, task_index=0, task_count=1)
    task_index, task_count = get_task_info()
    is_cloud_run = task_count > 1

    print_separator("═")
    print("  RETINA IMAGE TAGGER")
    print(f"  Filter Tanggal: {start_date_str} s/d {end_date_str}")
    if is_cloud_run:
        print(f"  Mode          : Cloud Run Jobs (Task {task_index + 1}/{task_count})")
    else:
        print(f"  Mode          : Service / Local")
    print(f"  Waktu mulai   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print_separator("═")

    # Koneksi database
    print("\n→ Menghubungkan ke database PostgreSQL ...")
    try:
        conn = get_db_connection()
        print("  ✓ Koneksi database berhasil.")
    except Exception as e:
        print(f"  ✗ Gagal terhubung ke database: {e}")
        return {"status": "error", "message": f"DB connection failed: {e}"}

    # Ambil data
    print("\n→ Mengambil data dari database ...")
    try:
        all_records = fetch_records(conn, start_date_str, end_date_str)
        print(f"  ✓ Ditemukan {len(all_records)} record total yang belum diproses.")
    except Exception as e:
        print(f"  ✗ Gagal mengambil data: {e}")
        conn.close()
        return {"status": "error", "message": f"Fetch records failed: {e}"}

    if not all_records:
        print("\n  Tidak ada data yang perlu diproses. Program selesai.")
        conn.close()
        return {"status": "success", "message": "No records to process"}

    # Bagi records ke task ini (idempotent berdasarkan urutan & modulo)
    records = split_records_for_task(all_records, task_index, task_count)
    if task_count > 1:
        print(f"  → Task {task_index + 1}/{task_count} akan memproses "
              f"{len(records)} dari {len(all_records)} record.")

    if not records:
        print("\n  Tidak ada record untuk task ini. Program selesai.")
        conn.close()
        return {"status": "success", "message": "No records for this task"}

    # Koneksi GCS
    print("\n→ Menginisialisasi Google Cloud Storage client ...")
    try:
        storage_client = storage.Client()
        print("  ✓ GCS client berhasil diinisialisasi.")
    except Exception as e:
        print(f"  ✗ Gagal menginisialisasi GCS client: {e}")
        conn.close()
        return {"status": "error", "message": f"GCS client failed: {e}"}

    # Variabel untuk statistik
    success, failed, skipped = 0, 0, 0
    processed_ids = {"uploadfile": [], "starterpackage": [], "voucherPackage": []}
    start_proc_time = time.time()
    start_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_all_records = len(all_records)

    # Proses gambar
    print()
    try:
        success, failed, skipped = process_records(
            records, storage_client, processed_ids, task_index, task_count, total_all_records
        )
    finally:
        # Lakukan update database massal di akhir (atau jika interupsi)
        if processed_ids["uploadfile"]:
            bulk_update_processed_records(conn, processed_ids["uploadfile"])
        if processed_ids["starterpackage"]:
            bulk_update_processed_starterpackage(conn, processed_ids["starterpackage"])
        if processed_ids["voucherPackage"]:
            bulk_update_processed_voucherpackage(conn, processed_ids["voucherPackage"])
        conn.close()

    end_proc_time = time.time()
    end_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    duration_sec = int(end_proc_time - start_proc_time)
    
    # Format durasi: HH:MM:SS
    hours, rem = divmod(duration_sec, 3600)
    minutes, seconds = divmod(rem, 60)
    duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    print("\n" + "═"*70)
    task_final_label = f" (TASK {task_index})" if task_count > 1 else ""
    print(f"  HASIL AKHIR PROSES{task_final_label}")
    print(f"  Waktu Mulai   : {start_time_str}")
    print(f"  Waktu Selesai : {end_time_str}")
    print(f"  Total Durasi  : {duration_str}")
    print(f"  Berhasil      : {success}")
    print(f"  Gagal         : {failed}")
    print(f"  Dilewati      : {skipped}")
    print("═"*70 + "\n")

    return {
        "status": "success",
        "details": {
            "start_time": start_time_str,
            "end_time": end_time_str,
            "duration": duration_str,
            "success_count": success,
            "failed_count": failed,
            "skipped_count": skipped
        }
    }


def run_tagging_by_id(record_id: int):
    """
    Proses satu record berdasarkan ID.
    """
    print_separator("═")
    print(f"  RETINA IMAGE TAGGER (SINGLE ID: {record_id})")
    print(f"  Waktu mulai   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print_separator("═")

    # Koneksi database
    print("\n→ Menghubungkan ke database PostgreSQL ...")
    try:
        conn = get_db_connection()
        print("  ✓ Koneksi database berhasil.")
    except Exception as e:
        print(f"  ✗ Gagal terhubung ke database: {e}")
        return {"status": "error", "message": f"DB connection failed: {e}"}

    # Ambil data
    print(f"\n→ Mengambil data untuk ID {record_id} ...")
    try:
        records = fetch_record_by_id(conn, record_id)
        if not records:
            print(f"  ✗ Record dengan ID {record_id} tidak ditemukan.")
            conn.close()
            return {"status": "error", "message": f"Record {record_id} not found"}
    except Exception as e:
        print(f"  ✗ Gagal mengambil data: {e}")
        conn.close()
        return {"status": "error", "message": f"Fetch record failed: {e}"}

    # Koneksi GCS
    print("\n→ Menginisialisasi Google Cloud Storage client ...")
    try:
        storage_client = storage.Client()
        print("  ✓ GCS client berhasil diinisialisasi.")
    except Exception as e:
        print(f"  ✗ Gagal menginisialisasi GCS client: {e}")
        conn.close()
        return {"status": "error", "message": f"GCS client failed: {e}"}

    # Variabel untuk statistik
    success, failed, skipped = 0, 0, 0
    processed_ids = {"uploadfile": [], "starterpackage": [], "voucherPackage": []}
    start_proc_time = time.time()

    # Proses gambar (reuse process_records; bisa lebih dari satu foto
    # jika record ini adalah imageCategory 'starter-package')
    print()
    try:
        success, failed, skipped = process_records(
            records, storage_client, processed_ids, 0, 1, len(records)
        )
    finally:
        # Lakukan update database massal di akhir
        if processed_ids["uploadfile"]:
            bulk_update_processed_records(conn, processed_ids["uploadfile"])
        if processed_ids["starterpackage"]:
            bulk_update_processed_starterpackage(conn, processed_ids["starterpackage"])
        if processed_ids["voucherPackage"]:
            bulk_update_processed_voucherpackage(conn, processed_ids["voucherPackage"])
        conn.close()

    end_proc_time = time.time()
    duration_sec = int(end_proc_time - start_proc_time)
    
    print("\n" + "═"*70)
    print(f"  HASIL AKHIR PROSES (ID: {record_id})")
    print(f"  Berhasil      : {success}")
    print(f"  Gagal         : {failed}")
    print("═"*70 + "\n")

    return {
        "status": "success" if success > 0 else "failed",
        "record_id": record_id
    }


def main():
    args = parse_args()
    run_tagging_process(args.start_date, args.end_date)


if __name__ == "__main__":
    main()
