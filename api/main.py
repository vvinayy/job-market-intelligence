"""
Job Market Intelligence API.

Run locally:
    uvicorn api.main:app --reload

Interactive documentation is generated automatically:
    http://localhost:8000/docs      (Swagger UI — try requests in-browser)
    http://localhost:8000/redoc     (reference-style)

Needs DATABASE_URL, or the individual PG* variables.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import init_pool, close_pool, fetch_value
from .routers import postings, reference, analytics, trends


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Open the connection pool once at startup rather than per request.
    init_pool()
    yield
    close_pool()


app = FastAPI(
    title="Job Market Intelligence API",
    description=(
        "Structured data on IT job postings collected from Naukri.\n\n"
        "**A note on what this data is.** Postings come from a fixed set of "
        "search terms and cities, sampled daily. Counts describe that sample, "
        "not the Indian IT market as a whole. Salary is disclosed by a minority "
        "of postings, and those employers are unlikely to be a random subset."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Wide open for now. Before deploying anywhere public, restrict
# allow_origins to the domains that should actually be able to call this.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(postings.router)
app.include_router(reference.router)
app.include_router(analytics.router)
app.include_router(trends.router)


@app.get("/health", tags=["meta"], summary="Service and database check")
def health():
    """Confirms the API is up AND that it can reach the database —
    a check that only reports on the process itself would stay green
    while every endpoint returned errors."""
    try:
        count = fetch_value("SELECT COUNT(*) FROM cleaned_postings")
        return {"status": "ok", "database": "connected", "postings": count}
    except Exception as e:
        return {"status": "degraded", "database": "unreachable", "detail": str(e)}


@app.get("/", tags=["meta"], summary="What this API offers")
def root():
    return {
        "name": "Job Market Intelligence API",
        "docs": "/docs",
        "endpoints": {
            "postings": "/postings — search individual job records",
            "reference": "/reference/{cities,states,roles,companies,skills}",
            "analytics": "/analytics/{summary,skills,roles,experience,locations,co-occurrence}",
            "trends": "/trends/{coverage,skills,movers,new-skills}",
        },
    }
