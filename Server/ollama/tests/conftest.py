import os

# The tests must not download speech models.
os.environ["TTS_PRELOAD"] = ""
