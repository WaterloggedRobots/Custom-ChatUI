import os

# The tests must not download speech models.
os.environ["TTS_PRELOAD"] = ""
# Nor start or remove real sandbox containers (test_sandbox.py turns it on against a fake Docker).
os.environ["SANDBOX_ENABLED"] = "0"
