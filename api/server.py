import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pathlib import Path
from api.routes.suggestions import router

OUTPUT_DIR = Path(__file__).parent.parent / "output"

app = FastAPI(title="Monitor API")
app.include_router(router)


@app.get("/")
def index():
    return RedirectResponse(url="/monitor_dashboard.html")


@app.get("/{filename}")
def static_file(filename: str):
    path = OUTPUT_DIR / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path)


if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=8080, reload=False)
