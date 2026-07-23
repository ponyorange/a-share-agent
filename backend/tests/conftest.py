import os


os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "MONGODB_URI",
    "mongodb://test-user:test-password@localhost:27017/sharedata_test",
)
os.environ.setdefault(
    "JWT_SECRET",
    "test-only-jwt-secret-with-at-least-32-bytes",
)
os.environ.setdefault(
    "LLM_ENCRYPTION_KEY",
    "test-only-llm-encryption-key-with-32-bytes",
)
os.environ.setdefault("DEV_SEED_ENABLED", "0")
