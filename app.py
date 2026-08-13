from fastapi import FastAPI, BackgroundTasks, Query, HTTPException
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
import os
from image_tagger import run_tagging_process, run_tagging_by_id, default_date_range

app = FastAPI(
    title="Retina Image Tagger API",
    description=(
        "API untuk memicu proses penambahan panel informasi (metadata) ke "
        "gambar yang tersimpan di Google Cloud Storage.\n\n"
        "Sumber gambar ditentukan otomatis berdasarkan `uploadfile.imageCategory`:\n"
        "- Kategori biasa → dibaca/ditulis dari `uploadfile.uploaded_filename` "
        "/ `uploadfile.uploaded_filename_withinfo`.\n"
        "- `starter-package` → dibaca/ditulis dari `starterpackage.photoUrl` "
        "/ `starterpackage.photoUrlWithInfo`.\n"
        "- `voucherfisik` → dibaca/ditulis dari `\"voucherPackage\".photoUrl` "
        "/ `\"voucherPackage\".photoUrlWithInfo`.\n\n"
        "Setiap proses dijalankan secara asynchronous di background agar "
        "request tidak timeout."
    ),
    version="1.0.0",
    openapi_tags=[
        {"name": "Health", "description": "Pengecekan status service."},
        {"name": "Tagging", "description": "Memicu proses image tagging."},
    ],
)


class HealthResponse(BaseModel):
    status: str = Field(..., example="healthy")
    timestamp: str = Field(..., example="2026-08-13T12:00:00.000000")


class TagStartedResponse(BaseModel):
    status: str = Field(..., example="started")
    message: str = Field(
        ...,
        example="Proses tagging untuk periode 2026-08-01 s/d 2026-08-13 telah dimulai di background.",
    )
    timestamp: str = Field(..., example="2026-08-13T12:00:00.000000")


class ErrorResponse(BaseModel):
    detail: str = Field(..., example="Format tanggal tidak valid. Gunakan YYYY-MM-DD.")


@app.get(
    "/health",
    tags=["Health"],
    summary="Cek status service",
    response_model=HealthResponse,
    response_description="Service dalam keadaan sehat.",
)
def health_check():
    """Endpoint sederhana untuk health check (liveness/readiness probe)."""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@app.get(
    "/tag",
    tags=["Tagging"],
    summary="Trigger tagging untuk range tanggal",
    response_model=TagStartedResponse,
    response_description="Proses tagging berhasil dimulai di background.",
    responses={400: {"model": ErrorResponse, "description": "Format tanggal tidak valid."}},
)
async def trigger_tagging(
    background_tasks: BackgroundTasks,
    start_date: Optional[str] = Query(
        None, description="Tanggal awal filter createdAt. Default: kemarin.", example="2026-08-01"
    ),
    end_date: Optional[str] = Query(
        None, description="Tanggal akhir filter createdAt (inklusif). Default: besok.", example="2026-08-13"
    ),
):
    """
    Trigger proses image tagging di background untuk semua record `uploadfile`
    yang `createdAt`-nya berada pada rentang `start_date` s/d `end_date`
    dan belum memiliki gambar ber-panel info.

    Jika `start_date`/`end_date` tidak diisi, default ke kemarin s/d besok.
    """
    # Isi default jika tidak diberikan
    default_start, default_end = default_date_range()
    start_date = start_date or default_start
    end_date = end_date or default_end

    # Validasi format tanggal sederhana
    try:
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal tidak valid. Gunakan YYYY-MM-DD.")

    # Jalankan di background agar tidak timeout
    background_tasks.add_task(run_tagging_process, start_date, end_date)

    return {
        "status": "started",
        "message": f"Proses tagging untuk periode {start_date} s/d {end_date} telah dimulai di background.",
        "timestamp": datetime.now().isoformat()
    }


@app.get(
    "/tag/{file_id}",
    tags=["Tagging"],
    summary="Trigger tagging untuk satu uploadfile ID",
    response_model=TagStartedResponse,
    response_description="Proses tagging berhasil dimulai di background.",
)
async def trigger_tagging_by_id(
    file_id: int,
    background_tasks: BackgroundTasks
):
    """
    Trigger proses image tagging di background untuk satu `uploadfile.id`.

    Jika `imageCategory` record tersebut adalah `starter-package` atau
    `voucherfisik`, seluruh foto terkait pada tabel `starterpackage` /
    `"voucherPackage"` akan ikut diproses.
    """
    # Jalankan di background
    background_tasks.add_task(run_tagging_by_id, file_id)

    return {
        "status": "started",
        "message": f"Proses tagging untuk ID {file_id} telah dimulai di background.",
        "timestamp": datetime.now().isoformat()
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
