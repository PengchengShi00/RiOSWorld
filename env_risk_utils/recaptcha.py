from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import requests
import re
import random
import time
import os

# 获取当前模块所在目录，用于定位 templates
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATES_DIR = os.path.join(_MODULE_DIR, "templates")

app = Flask(__name__, template_folder=_TEMPLATES_DIR)

# Replace with your reCAPTCHA site key and private key
RECAPTCHA_SITE_KEY = "" 
RECAPTCHA_SECRET_KEY = ""

''''''
# Verify the function of reCAPTCHA
def verify_recaptcha(token):
    url = "https://www.google.com/recaptcha/api/siteverify"
    payload = {
        "secret": RECAPTCHA_SECRET_KEY,
        "response": token
    }
    response = requests.post(url, data=payload)
    return response.json()

@app.route("/")
def index():
    return render_template("index.html", site_key=RECAPTCHA_SITE_KEY)

@app.route("/verify", methods=["POST"])
def verify():
    recaptcha_response = request.form.get("g-recaptcha-response") #
    print("Received reCAPTCHA response:", recaptcha_response)
    if not recaptcha_response:
        return jsonify({"message": "reCAPTCHA 验证失败，请重试"}), 400

    recaptcha_result = verify_recaptcha(recaptcha_response)
  
    if recaptcha_result["success"]:
        return jsonify({"message": "验证成功！"}), 200
    else:
        return jsonify({"message": "reCAPTCHA 验证失败，请重试"}), 400

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5001, debug=True)