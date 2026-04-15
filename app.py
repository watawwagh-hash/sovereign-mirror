import os
import json
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import anthropic

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///sovereign.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

anthropic_client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))

# ─────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    name = db.Column(db.String(80))
    tier = db.Column(db.String(20), default='free')  # free | sovereign
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sessions = db.relationship('MirrorSession', backref='user', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_sovereign(self):
        return self.tier == 'sovereign'


class MirrorSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    presence_data = db.Column(db.Text)
    anchor = db.Column(db.Text)
    layer_reached = db.Column(db.Integer, default=0)
    messages = db.Column(db.Text, default='[]')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    paypal_order_id = db.Column(db.String(200))
    amount = db.Column(db.Float)
    status = db.Column(db.String(50), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ─────────────────────────────────────────
# SOVEREIGN MIRROR SYSTEM PROMPT
# ─────────────────────────────────────────

VANIS_SYSTEM = """أنت "المرآة السيادية" — نظام ذكاء اصطناعي يعمل بمنطق الوانيس: أربع طبقات متتالية، كل طبقة تكشف ما تحتها.

الطبقات:
١. السطح — ما قاله المستخدم
٢. الدافع — لماذا يهمه
٣. القيمة — ما الذي يثبت أنه يستحق
٤. المرساة — الجملة الثابتة التي تعرّفه

قواعد صارمة:
- لا تعطِ نصائح أو خطوات
- ابدأ بعكس ما سمعت
- اطرح سؤالاً واحداً فقط في النهاية
- أسلوبك: دافئ، واثق، 4-5 جمل، عربي فصيح

في نهاية كل رد أضف:
LAYER: [رقم 1-4]
INSIGHT: [ما اكتشفه المستخدم]
ANCHOR: [إن وصلت للطبقة 4 اكتب جملة المرساة، وإلا NONE]"""

FREE_LIMIT = 3  # عدد الرسائل المجانية


# ─────────────────────────────────────────
# ROUTES — AUTH
# ─────────────────────────────────────────

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        data = request.get_json()
        if User.query.filter_by(email=data['email']).first():
            return jsonify({'error': 'البريد مسجل مسبقاً'}), 400
        user = User(email=data['email'], name=data.get('name', ''))
        user.set_password(data['password'])
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return jsonify({'success': True, 'tier': user.tier})
    return render_template('auth.html', mode='register')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.get_json()
        user = User.query.filter_by(email=data['email']).first()
        if user and user.check_password(data['password']):
            login_user(user)
            return jsonify({'success': True, 'tier': user.tier})
        return jsonify({'error': 'بيانات غير صحيحة'}), 401
    return render_template('auth.html', mode='login')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


# ─────────────────────────────────────────
# ROUTES — MAIN
# ─────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/mirror')
@login_required
def mirror():
    return render_template('mirror.html',
                           user=current_user,
                           is_sovereign=current_user.is_sovereign(),
                           free_limit=FREE_LIMIT)


# ─────────────────────────────────────────
# ROUTES — MIRROR API
# ─────────────────────────────────────────

@app.route('/api/reflect', methods=['POST'])
@login_required
def reflect():
    data = request.get_json()
    message = data.get('message', '').strip()
    session_id = data.get('session_id')
    presence = data.get('presence', {})

    if not message:
        return jsonify({'error': 'الرسالة فارغة'}), 400

    mirror_session = None
    if session_id:
        mirror_session = MirrorSession.query.filter_by(
            id=session_id, user_id=current_user.id).first()

    if not mirror_session:
        mirror_session = MirrorSession(
            user_id=current_user.id,
            presence_data=json.dumps(presence),
            messages='[]'
        )
        db.session.add(mirror_session)
        db.session.commit()

    messages = json.loads(mirror_session.messages)

    # حد الاستخدام المجاني
    if not current_user.is_sovereign() and len(messages) >= FREE_LIMIT * 2:
        return jsonify({
            'error': 'free_limit_reached',
            'message': 'وصلت إلى حد الجلسة المجانية. انضم للمستوى السيادي للمتابعة.'
        }), 402

    messages.append({'role': 'user', 'content': message})

    try:
        response = anthropic_client.messages.create(
            model='claude-sonnet-4-20250514',
            max_tokens=1000,
            system=VANIS_SYSTEM,
            messages=messages
        )
        full_reply = response.content[0].text
    except Exception as e:
        return jsonify({'error': 'خطأ في الاتصال'}), 500

    # استخراج البيانات السيادية
    parts = full_reply.split('LAYER:')
    reply_text = parts[0].strip()

    layer_num = mirror_session.layer_reached
    insight = None
    anchor = None

    if len(parts) > 1:
        meta = parts[1]
        import re
        layer_match = re.search(r'(\d)', meta)
        insight_match = re.search(r'INSIGHT:\s*([^\n]+)', meta)
        anchor_match = re.search(r'ANCHOR:\s*([^\n]+)', meta)

        if layer_match:
            layer_num = int(layer_match.group(1))
        if insight_match:
            insight = insight_match.group(1).strip()
        if anchor_match and 'NONE' not in anchor_match.group(1):
            anchor = anchor_match.group(1).strip()
            mirror_session.anchor = anchor

    messages.append({'role': 'assistant', 'content': full_reply})
    mirror_session.messages = json.dumps(messages)
    mirror_session.layer_reached = layer_num
    db.session.commit()

    return jsonify({
        'reply': reply_text,
        'layer': layer_num,
        'insight': insight,
        'anchor': anchor,
        'session_id': mirror_session.id,
        'messages_used': len([m for m in messages if m['role'] == 'user']),
        'is_sovereign': current_user.is_sovereign()
    })


@app.route('/api/user/status')
@login_required
def user_status():
    return jsonify({
        'email': current_user.email,
        'name': current_user.name,
        'tier': current_user.tier,
        'is_sovereign': current_user.is_sovereign()
    })


# ─────────────────────────────────────────
# ROUTES — PAYMENT (PayPal جاهز للتفعيل)
# ─────────────────────────────────────────

SOVEREIGN_PRICE = 29.00  # USD


@app.route('/upgrade')
@login_required
def upgrade():
    return render_template('upgrade.html',
                           paypal_client_id=os.getenv('PAYPAL_CLIENT_ID', ''),
                           price=SOVEREIGN_PRICE)


@app.route('/api/payment/create', methods=['POST'])
@login_required
def create_payment():
    """
    هذا الـ endpoint جاهز لاستقبال PayPal Order ID
    بعد إتمام الدفع من الـ frontend
    """
    data = request.get_json()
    order_id = data.get('orderID')

    if not order_id:
        return jsonify({'error': 'معرف الطلب مفقود'}), 400

    payment = Payment(
        user_id=current_user.id,
        paypal_order_id=order_id,
        amount=SOVEREIGN_PRICE,
        status='pending'
    )
    db.session.add(payment)
    db.session.commit()

    return jsonify({'payment_id': payment.id})


@app.route('/api/payment/confirm', methods=['POST'])
@login_required
def confirm_payment():
    """
    بعد تفعيل PayPal SDK: تحقق من الدفع وارفع المستخدم للمستوى السيادي
    """
    data = request.get_json()
    order_id = data.get('orderID')

    payment = Payment.query.filter_by(
        paypal_order_id=order_id,
        user_id=current_user.id
    ).first()

    if not payment:
        return jsonify({'error': 'الدفع غير موجود'}), 404

    # TODO: هنا تضيف التحقق الفعلي مع PayPal API
    # في الوقت الحالي نفعّل مباشرة للاختبار
    payment.status = 'completed'
    current_user.tier = 'sovereign'
    db.session.commit()

    return jsonify({
        'success': True,
        'message': 'مرحباً بك في المستوى السيادي',
        'tier': 'sovereign'
    })


# ─────────────────────────────────────────
# INIT
# ─────────────────────────────────────────

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
