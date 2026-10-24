"""Start the screening interface: python -m litmap.ui, then open http://localhost:8000."""
from litmap.ui.app import create_app

HOST = "0.0.0.0"  # all interfaces inside the container; docker-compose decides who can reach it
PORT = 8000

create_app().run(host=HOST, port=PORT)
