# Retina Image Tagger

Aplikasi Python untuk menambahkan informasi metadata ke gambar yang tersimpan di **Google Cloud Storage**. Data gambar diambil dari database **PostgreSQL**, kemudian setiap gambar dimodifikasi dengan menambahkan panel informasi berwarna putih di bagian bawah.

---

## Hasil Modifikasi

| Sebelum | Sesudah |
|---|---|
| `gs://bucket/path/foo.jpg` | `gs://bucket/path/foo_with_info.jpg` |

Panel putih yang ditambahkan di bawah gambar berisi:

- **Tanggal Upload** — kolom `createdAt`
- **Diunggah Oleh** — `uploaded_by_fullname (uploaded_by_email)`
- **Toko** — `store_name`
- **Lokasi** — `store_city, store_region, store_branch, store_area`

---

## Prasyarat

- **File `.env`**: Buat file `.env` di root folder dengan isi berikut:
  ```env
  DB_HOST=<dbhost>
  DB_NAME=<dbname>
  DB_USER=<dbuser>
  DB_PASS=<dbpassword>
  DB_PORT=<dbport>
  GCS_BUCKET_NAME=<bucketname>
  ```
- **Di Compute Engine**: VM dengan service account yang memiliki peran `Storage Object Admin` pada bucket tersebut
- **Di lokal**: Set environment variable `GOOGLE_APPLICATION_CREDENTIALS` ke path file JSON service account

---

## Setup di Compute Engine (Pertama Kali)

```bash
# Clone / upload kode ke VM
git clone <repo-url>
cd retina-image-tagging

# Jalankan setup (install dependencies)
chmod +x setup.sh
./setup.sh
```

---

## Deploy dan Jalankan di Cloud Run Jobs (Paralel)

### 1. Build dan push Docker image ke Artifact Registry

```bash
PROJECT_ID="<your-gcp-project-id>"
REGION="asia-southeast2"
REPO="retina-image-tagger"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/image-tagger:latest"

# Buat Artifact Registry repository (sekali)
gcloud artifacts repositories create ${REPO} \
  --repository-format=docker \
  --location=${REGION}

# Build & push image
gcloud builds submit --tag ${IMAGE} .
```

### 2. Buat Cloud Run Job

```bash
gcloud run jobs create image-tagger-job \
  --image=${IMAGE} \
  --region=${REGION} \
  --service-account=<SERVICE_ACCOUNT_EMAIL> \
  --set-env-vars="CLOUD_RUN_TASK_COUNT=4" \
  --args="--start-date,2025-06-01,--end-date,2025-06-30"
```

> **Catatan**: `--service-account` harus memiliki roles `Storage Object Admin` pada bucket dan akses ke Cloud SQL / PostgreSQL.

### 3. Jalankan Job (paralel)

```bash
# Jalankan dengan 4 task paralel
gcloud run jobs execute image-tagger-job \
  --region=${REGION} \
  --tasks=4 \
  --wait
```

### Cara Kerja Pembagian Task

Semua task menjalankan image yang sama dan menerima parameter `--start-date` / `--end-date` yang sama. Perbedaannya:

| Env Var | Deskripsi |
|---|---|
| `CLOUD_RUN_TASK_INDEX` | Indeks task ini (0-based), di-inject otomatis oleh Cloud Run |
| `CLOUD_RUN_TASK_COUNT` | Total jumlah task, sama dengan nilai `--tasks` |

Script membagi records menggunakan **slice modulo**:
- Task 0 → records ke-0, 4, 8, 12, ...
- Task 1 → records ke-1, 5, 9, 13, ...
- Task 2 → records ke-2, 6, 10, 14, ...
- Task 3 → records ke-3, 7, 11, 15, ...

Setiap task berjalan **independen** tanpa perlu koordinasi. Karena ada pengecekan idempotent (file `_with_info` sudah ada → dilewati), aman dijalankan ulang.

---

## Cara Menjalankan

### Aktifkan virtual environment (jika menggunakan venv)
```bash
source venv/bin/activate
```

### Jalankan aplikasi
```bash
python3 image_tagger.py --start-date YYYY-MM-DD --end-date YYYY-MM-DD
```

### Contoh
```bash
# Proses gambar yang diupload di bulan Juni 2025
python3 image_tagger.py --start-date 2025-06-01 --end-date 2025-06-30

# Proses gambar satu hari tertentu
python3 image_tagger.py --start-date 2025-06-15 --end-date 2025-06-15

# Proses seluruh tahun 2025
python3 image_tagger.py --start-date 2025-01-01 --end-date 2025-12-31
```

---

## Output Aplikasi

```
══════════════════════════════════════════════════════════════════════
  RETINA IMAGE TAGGER
  Filter Tanggal: 2025-06-01 s/d 2025-06-30
  Waktu mulai   : 2025-07-01 08:00:00
══════════════════════════════════════════════════════════════════════

→ Menghubungkan ke database PostgreSQL ...
  ✓ Koneksi database berhasil.

→ Mengambil data dari database ...
  ✓ Ditemukan 42 record dengan gambar.

──────────────────────────────────────────────────────────────────────
  Total gambar yang akan diproses: 42
──────────────────────────────────────────────────────────────────────

[1/42] Memproses ...
  File      : gs://retail-intelligence-bucket/prod-uploads-june-2025/poster/foo.jpg
  Toko      : Superindo Bogor Plaza
  Tanggal   : 15 Juni 2025, 09:30 WIB
  ↓  Mengunduh dari GCS ...
  ✏  Menambahkan panel informasi ...
  ↑  Mengunggah ke GCS: gs://retail-intelligence-bucket/prod-uploads-june-2025/poster/foo_with_info.jpg
  ✓  Selesai → gs://...

══════════════════════════════════════════════════════════════════════
  SELESAI
  Total   : 42
  Berhasil: 41
  Dilewati: 1 (sudah ada)
  Gagal   : 0
══════════════════════════════════════════════════════════════════════
```

---

## Struktur File

```
retina-image-tagging/
├── image_tagger.py   # Script utama
├── requirements.txt  # Dependensi Python
├── setup.sh          # Script setup Compute Engine
├── .env              # Konfigurasi database & GCS
└── README.md         # Dokumentasi ini
```

---

## Catatan Penting

- **Gambar asli tidak ditimpa** — file baru dibuat di GCS dengan suffix `_with_info`.
- **Idempotent** — jika file `_with_info` sudah ada di GCS, gambar tersebut dilewati (tidak diproses ulang).
- **Autentikasi GCS** — di Compute Engine menggunakan service account VM secara otomatis. Di lokal, set `GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json`.
- **Filter tanggal inklusif** — `--end-date` menyertakan hari tersebut (hingga 23:59:59).
