release: flask db upgrade
web: python -m gunicorn wsgi:app -w 2 -k gthread --threads 8 -t 120 -b 0.0.0.0:$PORT
