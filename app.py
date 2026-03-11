from fastapi import FastAPI, BackgroundTasks, Query, HTTPException
from datetime import datetime
import os
from image_tagger import run_tagging_process

app = FastAPI(title="Retina Image Tagger API")

@app.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/tag")
async def trigger_tagging(
    background_tasks: BackgroundTasks,
    start_date: str = Query(..., description="Format: YYYY-MM-DD"),
    end_date: str = Query(..., description="Format: YYYY-MM-DD")
):
    """
    Trigger proses image tagging di background.
    """
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

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
