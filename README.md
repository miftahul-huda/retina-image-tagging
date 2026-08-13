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

Pilih Dockerfile sesuai kebutuhan:
- **`Dockerfile.service`**: Untuk Cloud Run Service (REST API)
- **`Dockerfile.job`**: Untuk Cloud Run Jobs (CLI)

```bash
PROJECT_ID="telkomsel-retail-intelligence"
REGION="asia-southeast2"
REPO="retina-image-tagger"

# Build untuk Service
SERVICE_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/image-tagger-service:latest"
gcloud builds submit --config=cloudbuild-service.yaml --substitutions=_IMAGE_NAME=${SERVICE_IMAGE} .

# Build untuk Job
JOB_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/image-tagger-job:latest"
gcloud builds submit --config=cloudbuild-job.yaml --substitutions=_IMAGE_NAME=${JOB_IMAGE} .
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

## Update Deployment (Cloud Run Service & Job)

Bagian sebelumnya menjelaskan cara membuat resource dari nol (memakai repo
Artifact Registry `retina-image-tagger`). Bagian ini untuk kasus yang lebih
sering terjadi: **Service** (`retina-image-tagging`) dan **Job**
(`retina-image-tagging-job`) **sudah ada** di GCP, dan kamu hanya perlu
mendorong kode terbaru ke sana.

> **Kenapa repo-nya beda dari bagian di atas?**
> Service & Job yang sekarang berjalan awalnya di-deploy pakai
> `gcloud run deploy --source .`, yang otomatis membuat & memakai repo
> bernama **`cloud-run-source-deploy`** — bukan repo `retina-image-tagger`
> yang dibuat manual di bagian "Deploy dan Jalankan di Cloud Run Jobs".
> Supaya image baru terpasang ke Service/Job yang sama (bukan bikin
> resource baru), kamu **harus** push ke repo yang sama dengan yang
> dipakai resource tersebut sekarang. Cek repo yang sedang dipakai dengan:
> ```bash
> gcloud run services describe retina-image-tagging \
>   --region=asia-southeast2 --format="value(spec.template.spec.containers[0].image)"
> gcloud run jobs describe retina-image-tagging-job \
>   --region=asia-southeast2 --format="value(spec.template.spec.template.spec.containers[0].image)"
> ```
> Repo-nya adalah bagian di antara `.../pkg.dev/PROJECT_ID/` dan
> `/nama-image:tag` pada output di atas. Saat ini keduanya berada di
> `cloud-run-source-deploy`, sehingga variabel `REPO` di bawah diisi
> nilai tersebut, **bukan** `retina-image-tagger`.

### Konfigurasi bersama

```bash
PROJECT_ID="telkomsel-retail-intelligence"
REGION="asia-southeast2"
TAG="manual-$(date +%Y%m%d-%H%M%S)"
REPO="cloud-run-source-deploy"   # repo yang SAAT INI dipakai Service & Job (lihat penjelasan di atas)
```

**Anatomi path image** (`${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/nama-image:${TAG}`):

| Bagian | Contoh | Artinya |
|---|---|---|
| `${REGION}-docker.pkg.dev` | `asia-southeast2-docker.pkg.dev` | Host registry Artifact Registry untuk region tsb |
| `${PROJECT_ID}` | `telkomsel-retail-intelligence` | Project GCP |
| `${REPO}` | `cloud-run-source-deploy` | **Repository** Artifact Registry — "folder" yang wajib ada sebelum bisa push image apa pun. Satu repo bisa menampung banyak image berbeda (mis. `retina-image-tagging`, `retina-image-tagging-job`, dan image service lain di project ini) |
| `nama-image` | `retina-image-tagging` | Nama image di dalam repo tsb |
| `${TAG}` | `manual-20260813-...` | Versi/tag image |

`REPO` **bukan** nama image — jadi harus diisi nama repo Artifact Registry yang benar (lihat cara verifikasi di atas), bukan disamakan dengan nama Service/Job.

### Update Cloud Run Service (`retina-image-tagging`)

Service menjalankan REST API (FastAPI) dari `Dockerfile.service`.

```bash
# 1. Build & push image baru
SERVICE_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/retina-image-tagging:${TAG}"
gcloud builds submit --config=cloudbuild-service.yaml \
  --substitutions=_IMAGE_NAME=${SERVICE_IMAGE} \
  --project=${PROJECT_ID} .

# 2. Deploy image ke service yang sudah ada
gcloud run deploy retina-image-tagging \
  --image=${SERVICE_IMAGE} \
  --region=${REGION} \
  --project=${PROJECT_ID}
```

> `--image` hanya mengganti image container. Env vars, resource limits, dan konfigurasi lain dari revisi sebelumnya otomatis dipertahankan.

### Update Cloud Run Job (`retina-image-tagging-job`)

Job menjalankan CLI (`image_tagger.py`) dari `Dockerfile.job`, dipakai untuk proses batch paralel (lihat bagian [Cara Kerja Pembagian Task](#cara-kerja-pembagian-task)).

```bash
# 1. Build & push image baru
JOB_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/retina-image-tagging-job:${TAG}"
gcloud builds submit --config=cloudbuild-job.yaml \
  --substitutions=_IMAGE_NAME=${JOB_IMAGE} \
  --project=${PROJECT_ID} .

# 2. Update job yang sudah ada dengan image baru
gcloud run jobs update retina-image-tagging-job \
  --image=${JOB_IMAGE} \
  --region=${REGION} \
  --project=${PROJECT_ID}

# 3. (Opsional) jalankan job untuk menguji perubahan
gcloud run jobs execute retina-image-tagging-job \
  --region=${REGION} \
  --project=${PROJECT_ID} \
  --wait
```

> `gcloud run jobs update --image` hanya mengganti image; `taskCount`, resource limits, timeout, dan `--args` yang sudah diset sebelumnya tetap dipertahankan.

### Catatan

- File `.gcloudignore` di root sudah mengecualikan `venv/`, `.git/`, dan `.env*` agar tidak ikut ter-upload saat `gcloud builds submit` (mempercepat build & menghindari kebocoran kredensial).
- Lihat resource yang tersedia dengan `gcloud run services list --region=${REGION}` dan `gcloud run jobs list --region=${REGION}`.

---

## REST API (FastAPI)

Selain sebagai CLI / Cloud Run Job, aplikasi ini juga bisa berjalan sebagai REST API.

### Cara Menjalankan Lokal

```bash
uvicorn app:app --host 0.0.0.0 --port 8080 --reload
```

### Endpoint API

#### 1. Health Check
`GET /health`
Mengecek apakah server berjalan.

#### 2. Trigger Tagging (Range Tanggal)
`GET /tag?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`

**Parameter** (keduanya opsional — jika tidak diisi, default ke **kemarin s/d besok**):
- `start_date`: Tanggal awal (format YYYY-MM-DD)
- `end_date`: Tanggal akhir (format YYYY-MM-DD)

**Response**:
Server akan segera mengembalikan response sukses dan menjalankan proses tagging di **background**.

#### 3. Trigger Tagging (Single ID)
`GET /tag/{file_id}`

**Parameter**:
- `file_id`: ID unik record di database.

**Response**:
Server akan segera mengembalikan response sukses dan menjalankan proses tagging untuk ID tersebut di **background**.

```json
{
  "status": "started",
  "message": "Proses tagging untuk ID 12345 telah dimulai di background.",
  "timestamp": "2026-03-11T15:45:00.000000"
}
```

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

`--start-date` dan `--end-date` opsional — jika salah satu/keduanya tidak diisi, default ke **kemarin s/d besok**.

### Contoh
```bash
# Proses gambar yang diupload di bulan Juni 2025
python3 image_tagger.py --start-date 2025-06-01 --end-date 2025-06-30

# Proses gambar satu hari tertentu
python3 image_tagger.py --start-date 2025-06-15 --end-date 2025-06-15

# Proses seluruh tahun 2025
python3 image_tagger.py --start-date 2025-01-01 --end-date 2025-12-31

# Tanpa parameter -> otomatis kemarin s/d besok
python3 image_tagger.py
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
