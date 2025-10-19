from flask import Flask

app = Flask(__name__)

@app.route("/check")
def check():
    return "ALLOW"  # Change to "DENY" to block app remotely

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
