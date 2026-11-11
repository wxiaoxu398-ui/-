import os

# Normal launch uses the prepared model; downloading belongs to prepare-medrag.cmd.
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
os.environ.setdefault('DO_NOT_TRACK', '1')
import uvicorn
if __name__ == '__main__':
    uvicorn.run('app.main:app', host='127.0.0.1', port=8000)
