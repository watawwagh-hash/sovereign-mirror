import os
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'sovereign_secret')

# --- ربط الواجهات السيادية التي صنعتها ---

@app.route('/')
def home():
    # فتح بوابة الدخول السيادية
    return render_template('index.html')

@app.route('/session')
@app.route('/chat')
def session():
    # فتح غرفة التجربة والتردد
    return render_template('mirror.html')

@app.route('/upgrade')
def upgrade():
    # فتح منطق الربح والترقية
    return render_template('upgrade.html')

# --- نهاية الربط ---

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
