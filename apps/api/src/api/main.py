from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import get_settings

app = FastAPI(title="Future Predict API")

app_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=app_settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "env": settings.app_env}
