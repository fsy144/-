from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from datetime import datetime, date
from werkzeug.security import generate_password_hash, check_password_hash
import os
import base64
import io
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from functools import wraps
import hmac
from ip_location import lookup_ip_location


_LOCATION_ENGLISH = {
    '中国': 'China', '美国': 'United States', '新西兰': 'New Zealand', '澳大利亚': 'Australia',
    '加拿大': 'Canada', '日本': 'Japan', '韩国': 'South Korea', '新加坡': 'Singapore',
    '英国': 'United Kingdom', '德国': 'Germany', '法国': 'France', '内网IP': 'Private Network',
    '辽宁省': 'Liaoning', '江苏省': 'Jiangsu', '浙江省': 'Zhejiang', '广东省': 'Guangdong',
    '山东省': 'Shandong', '福建省': 'Fujian', '四川省': 'Sichuan', '湖北省': 'Hubei',
    '湖南省': 'Hunan', '河北省': 'Hebei', '河南省': 'Henan', '安徽省': 'Anhui',
    '陕西省': 'Shaanxi', '山西省': 'Shanxi', '黑龙江省': 'Heilongjiang', '吉林省': 'Jilin',
    '云南省': 'Yunnan', '贵州省': 'Guizhou', '广西': 'Guangxi', '海南省': 'Hainan',
    '北京市': 'Beijing', '上海市': 'Shanghai', '天津市': 'Tianjin', '重庆市': 'Chongqing',
    '大连市': 'Dalian', '本机/内网': 'Local / Private Network',
}


def format_location_english(country, province, city):
    """详情页只输出英文；未收录的中文地区不会原样展示。"""
    values = []
    for value in (country, province, city):
        if not value:
            continue
        translated = _LOCATION_ENGLISH.get(value)
        if translated:
            values.append(translated)
        elif not any('\u4e00' <= char <= '\u9fff' for char in value):
            values.append(value)
    return ' / '.join(values) or '-'

app = Flask(__name__)
app.config.from_pyfile('config.py')
app.config['SESSION_COOKIE_SECURE'] = True

os.makedirs(app.instance_path, exist_ok=True)
os.makedirs(os.path.join(app.static_folder, 'uploads'), exist_ok=True)
os.makedirs(os.path.join(app.static_folder, 'avatars'), exist_ok=True)

db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(20), default='user')
    permissions = db.Column(db.String(200), default='')
    avatar_path = db.Column(db.String(200), default='')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def has_permission(self, permission):
        if self.role == 'admin':
            return True
        if self.permissions:
            perms = [p.strip() for p in self.permissions.split(',')]
            return permission in perms
        return False

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    barcode = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    spec = db.Column(db.String(50))
    unit = db.Column(db.String(20), default='个')
    stock = db.Column(db.Integer, default=0)
    create_time = db.Column(db.DateTime, default=datetime.now)
    update_time = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

class StockRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    type = db.Column(db.String(10), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    operator = db.Column(db.String(50))
    remark = db.Column(db.String(200))
    waybill_number = db.Column(db.String(100))
    photo_path = db.Column(db.String(200))
    parent_id = db.Column(db.Integer, db.ForeignKey('stock_record.id'), nullable=True)
    production_date = db.Column(db.Date)
    expiry_date = db.Column(db.Date)
    platform = db.Column(db.String(50))
    create_time = db.Column(db.DateTime, default=datetime.now)

    product = db.relationship('Product', backref=db.backref('records', lazy=True))
    sub_records = db.relationship('StockRecord', backref=db.backref('parent', remote_side=[id]), lazy=True)


# 防伪查询模块的数据表。它们与现有的库存产品、库存记录完全独立，
# 因此增加本模块不会改变 .cn 现有的库存业务数据。
class AntiFakeCode(db.Model):
    __tablename__ = 'anti_fake_codes'

    id = db.Column(db.Integer, primary_key=True)
    qr_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    serial_no = db.Column(db.String(100), unique=True, nullable=False, index=True)
    product_name = db.Column(db.String(200))
    product_spec = db.Column(db.String(100))
    batch_no = db.Column(db.String(100))
    package_ratio = db.Column(db.String(50))
    scan_count = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    scan_events = db.relationship(
        'AntiFakeScanEvent', backref='anti_fake_code', lazy=True,
        cascade='all, delete-orphan'
    )


class AntiFakeScanEvent(db.Model):
    __tablename__ = 'anti_fake_scan_events'

    id = db.Column(db.Integer, primary_key=True)
    # .com 的 scan_logs.id 或实时上报 ID，用于支持安全重试而不重复记一条扫码。
    source_event_id = db.Column(db.String(100), unique=True, index=True)
    qr_id = db.Column(
        db.String(64), db.ForeignKey('anti_fake_codes.qr_id'), nullable=False, index=True
    )
    scan_time = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    ip_address = db.Column(db.String(64), nullable=False, index=True)
    country = db.Column(db.String(100))
    province = db.Column(db.String(100))
    city = db.Column(db.String(100))
    platform = db.Column(db.String(100))
    scan_channel = db.Column(db.String(100))
    verification_result = db.Column(db.String(30), nullable=False, default='success', index=True)
    user_agent = db.Column(db.Text)


class AntiFakeSyncState(db.Model):
    """记录只读同步进度，避免重复导入 .com 的 scan_logs。"""
    __tablename__ = 'anti_fake_sync_state'

    id = db.Column(db.Integer, primary_key=True)
    source_name = db.Column(db.String(50), unique=True, nullable=False)
    last_code_id = db.Column(db.Integer, default=0, nullable=False)
    last_scan_log_id = db.Column(db.Integer, default=0, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

def ensure_anti_fake_schema():
    """为已存在的本地测试库补齐防伪模块新增列，不触碰库存业务表。"""
    columns = {column['name'] for column in inspect(db.engine).get_columns('anti_fake_scan_events')}
    if 'source_event_id' not in columns:
        db.session.execute(text('ALTER TABLE anti_fake_scan_events ADD COLUMN source_event_id VARCHAR(100)'))
        db.session.execute(text(
            'CREATE UNIQUE INDEX IF NOT EXISTS ix_anti_fake_scan_events_source_event_id '
            'ON anti_fake_scan_events (source_event_id)'
        ))
        db.session.commit()


with app.app_context():
    db.create_all()
    ensure_anti_fake_schema()
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', role='admin', permissions='delete,manage_users,adjust')
        admin.set_password('fsy824phatma')
        db.session.add(admin)
        db.session.commit()
    print("数据库初始化成功！")

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def permission_required(permission):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            user = User.query.get(session['user_id'])
            if not user or not user.has_permission(permission):
                if request.is_json:
                    return jsonify({'success': False, 'message': '权限不足'}), 403
                return render_template('error.html', message='您没有权限访问此页面，请联系管理员。'), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.before_request
def require_login():
    # .com 的扫码事件由本机服务令牌鉴权，不能被重定向到后台登录页。
    allowed_endpoints = ['login', 'static', 'receive_anti_fake_event']
    if request.endpoint in allowed_endpoints:
        return
    if 'user_id' not in session:
        return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session['user_id'] = user.id
            session['username'] = user.username
            session['role'] = user.role
            session['avatar'] = user.avatar_path or ''
            session['permissions'] = user.permissions or ''
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='用户名或密码错误')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username or not password:
            return render_template('register.html', error='用户名和密码不能为空')
        if User.query.filter_by(username=username).first():
            return render_template('register.html', error='用户名已存在')
        user = User(username=username, role='user')
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        session['user_id'] = user.id
        session['username'] = user.username
        session['role'] = user.role
        session['avatar'] = ''
        session['permissions'] = user.permissions or ''
        return redirect(url_for('index'))
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/api/avatar/upload', methods=['POST'])
def upload_avatar():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '没有文件'})
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': '文件名为空'})
    user = User.query.get(session['user_id'])
    if not user:
        return jsonify({'success': False, 'message': '用户不存在'})
    ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext not in ['jpg', 'jpeg', 'png', 'gif']:
        return jsonify({'success': False, 'message': '仅支持图片格式'})
    filename = f"avatar_{user.id}.{ext}"
    filepath = os.path.join(app.static_folder, 'avatars', filename)
    file.save(filepath)
    user.avatar_path = f'avatars/{filename}'
    db.session.commit()
    session['avatar'] = user.avatar_path
    return jsonify({'success': True, 'avatar_url': url_for('static', filename=user.avatar_path)})

@app.route('/')
def index():
    inventory_rows = Product.query.order_by(Product.id.desc()).all()

    user = None
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
    can_delete = user.has_permission('delete') if user else False

    return render_template('index.html', inventory_rows=inventory_rows, can_delete=can_delete)

@app.route('/records/<record_type>')
def records(record_type):
    if record_type not in ['in', 'out']:
        return redirect(url_for('index'))

    if record_type == 'in':
        records_list = StockRecord.query.filter(StockRecord.type.in_(['in', 'adjust_in'])).order_by(StockRecord.create_time.desc()).all()
    else:
        records_list = StockRecord.query.filter(StockRecord.type.in_(['out', 'adjust_out']), StockRecord.parent_id == None).order_by(StockRecord.create_time.desc()).all()

    title = '入库记录' if record_type == 'in' else '出库记录'
    return render_template('records.html', records=records_list, record_type=record_type, title=title)

@app.route('/records/<record_type>/download', methods=['POST'])
def download_records(record_type):
    if record_type not in ['in', 'out']:
        return '参数错误', 400

    selected_fields = request.form.getlist('fields')
    if not selected_fields:
        return '未选择字段', 400

    if record_type == 'in':
        records_list = StockRecord.query.filter(StockRecord.type.in_(['in', 'adjust_in'])).order_by(StockRecord.create_time.desc()).all()
        filename = '入库记录.xlsx'
    else:
        records_list = StockRecord.query.filter(StockRecord.type.in_(['out', 'adjust_out']), StockRecord.parent_id == None).order_by(StockRecord.create_time.desc()).all()
        filename = '出库记录.xlsx'

    field_defs = {
        '操作时间': lambda r: r.create_time.strftime('%Y-%m-%d %H:%M:%S') if r.create_time else '',
        '产品条码': lambda r: r.product.barcode if r.product else '',
        '产品名称': lambda r: r.product.name if r.product else '',
        '规格': lambda r: r.product.spec if r.product else '',
        '数量': lambda r: f"{r.quantity} {r.product.unit if r.product else ''}",
        '操作人': lambda r: r.operator or '',
        '生产日期': lambda r: r.production_date.strftime('%Y-%m-%d') if r.production_date else '',
        '到期日期': lambda r: r.expiry_date.strftime('%Y-%m-%d') if r.expiry_date else '',
        '平台': lambda r: r.platform or '',
        '备注': lambda r: r.remark or '',
        '运单号': lambda r: r.waybill_number or ''
    }

    wb = Workbook()
    ws = wb.active
    ws.title = '记录'
    ws.append(selected_fields)

    for rec in records_list:
        row = [field_defs[f](rec) for f in selected_fields]
        ws.append(row)

    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center')

    for col in ws.columns:
        max_len = 0
        col_letter = col[0].column_letter
        for cell in col:
            try:
                if cell.value and len(str(cell.value)) > max_len:
                    max_len = len(str(cell.value))
            except:
                pass
        ws.column_dimensions[col_letter].width = min(max_len + 2, 30)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/inventory')
def inventory_page():
    user = User.query.get(session.get('user_id'))
    can_adjust = user.has_permission('adjust') if user else False
    return render_template('inventory.html', can_adjust=can_adjust)


@app.route('/anti-fake')
def anti_fake_overview():
    """所有已登录 .cn 后台的用户均可查看的防伪扫码汇总页。"""
    keyword = request.args.get('keyword', '').strip()
    result = request.args.get('result', '').strip()
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()
    page = request.args.get('page', 1, type=int)

    query = AntiFakeScanEvent.query.join(AntiFakeCode)
    if keyword:
        pattern = f'%{keyword}%'
        query = query.filter(db.or_(
            AntiFakeCode.serial_no.ilike(pattern),
            AntiFakeCode.qr_id.ilike(pattern),
            AntiFakeScanEvent.ip_address.ilike(pattern),
            AntiFakeCode.product_name.ilike(pattern)
        ))
    if result:
        query = query.filter(AntiFakeScanEvent.verification_result == result)
    if start_date:
        try:
            query = query.filter(AntiFakeScanEvent.scan_time >= datetime.strptime(start_date, '%Y-%m-%d'))
        except ValueError:
            start_date = ''
    if end_date:
        try:
            end_at = datetime.strptime(end_date, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
            query = query.filter(AntiFakeScanEvent.scan_time <= end_at)
        except ValueError:
            end_date = ''

    events = query.order_by(AntiFakeScanEvent.scan_time.desc()).paginate(
        page=page, per_page=30, error_out=False
    )
    summary = {
        'code_count': AntiFakeCode.query.count(),
        'scan_count': AntiFakeScanEvent.query.count(),
        'today_count': AntiFakeScanEvent.query.filter(
            AntiFakeScanEvent.scan_time >= datetime.combine(date.today(), datetime.min.time())
        ).count(),
        'warning_count': AntiFakeScanEvent.query.filter_by(verification_result='warning').count(),
    }
    return render_template(
        'anti_fake_overview.html', events=events, summary=summary, keyword=keyword,
        result=result, start_date=start_date, end_date=end_date
    )


@app.route('/anti-fake/codes/<int:code_id>')
def anti_fake_code_detail(code_id):
    """单个防伪码的产品资料与完整扫码时间线。"""
    code = AntiFakeCode.query.get_or_404(code_id)
    events = AntiFakeScanEvent.query.filter_by(qr_id=code.qr_id).order_by(
        AntiFakeScanEvent.scan_time.desc()
    ).all()
    return render_template(
        'anti_fake_detail.html', code=code, events=events,
        format_location_english=format_location_english
    )


@app.route('/api/anti-fake/events', methods=['POST'])
def receive_anti_fake_event():
    """供 .com 后续在扫码完成后向本机 phatma.cn 上报一条事件。"""
    expected_token = os.environ.get('ANTI_FAKE_SYNC_TOKEN')
    supplied_token = request.headers.get('X-Anti-Fake-Token', '')
    if not expected_token or not hmac.compare_digest(supplied_token, expected_token):
        return jsonify({'success': False, 'message': '未授权'}), 403

    payload = request.get_json(silent=True) or {}
    required = ('event_id', 'qr_id', 'serial_no', 'ip_address')
    missing = [field for field in required if not str(payload.get(field, '')).strip()]
    if missing:
        return jsonify({'success': False, 'message': '缺少字段: ' + ', '.join(missing)}), 400

    event_id = str(payload['event_id']).strip()
    if AntiFakeScanEvent.query.filter_by(source_event_id=event_id).first():
        return jsonify({'success': True, 'duplicate': True})

    qr_id = str(payload['qr_id']).strip()
    code = AntiFakeCode.query.filter_by(qr_id=qr_id).first()
    if code is None:
        code = AntiFakeCode(qr_id=qr_id, serial_no=str(payload['serial_no']).strip())
        db.session.add(code)

    try:
        scan_time = datetime.fromisoformat(str(payload.get('scan_time', '')).replace('Z', '+00:00'))
    except ValueError:
        scan_time = datetime.now()

    result = str(payload.get('verification_result', 'success')).strip()
    if result not in ('success', 'warning', 'invalid'):
        return jsonify({'success': False, 'message': 'verification_result 无效'}), 400

    for field in ('product_name', 'product_spec', 'batch_no', 'package_ratio'):
        if payload.get(field) is not None:
            setattr(code, field, str(payload[field]).strip() or None)
    if payload.get('scan_count') is not None:
        try:
            code.scan_count = max(code.scan_count, int(payload['scan_count']))
        except (TypeError, ValueError):
            pass

    location = lookup_ip_location(str(payload['ip_address']).strip())

    db.session.add(AntiFakeScanEvent(
        source_event_id=event_id,
        qr_id=qr_id,
        scan_time=scan_time,
        ip_address=str(payload['ip_address']).strip(),
        country=str(payload.get('country', '')).strip() or location['country'],
        province=str(payload.get('province', '')).strip() or location['province'],
        city=str(payload.get('city', '')).strip() or location['city'],
        platform=str(payload.get('platform', '')).strip() or None,
        scan_channel=str(payload.get('scan_channel', '')).strip() or None,
        verification_result=result,
        user_agent=str(payload.get('user_agent', '')).strip() or None,
    ))
    db.session.commit()
    return jsonify({'success': True, 'duplicate': False}), 201

@app.route('/admin/users')
@permission_required('manage_users')
def users_management():
    return render_template('users_management.html')

@app.route('/api/admin/users')
@permission_required('manage_users')
def get_users():
    users = User.query.all()
    data = [{'id': u.id, 'username': u.username, 'role': u.role, 'permissions': u.permissions} for u in users]
    return jsonify({'success': True, 'users': data})

@app.route('/api/admin/users', methods=['POST'])
@permission_required('manage_users')
def add_user():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', 'user')
    permissions = request.form.get('permissions', '')
    if not username or not password:
        return jsonify({'success': False, 'message': '用户名和密码不能为空'})
    if User.query.filter_by(username=username).first():
        return jsonify({'success': False, 'message': '用户名已存在'})
    user = User(username=username, role=role, permissions=permissions)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return jsonify({'success': True, 'message': '用户添加成功'})

@app.route('/api/admin/users/<int:user_id>', methods=['PUT'])
@permission_required('manage_users')
def update_user(user_id):
    user = User.query.get_or_404(user_id)
    role = request.form.get('role', user.role)
    permissions = request.form.get('permissions', user.permissions)
    user.role = role
    user.permissions = permissions
    db.session.commit()
    return jsonify({'success': True, 'message': '用户权限更新成功'})

@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@permission_required('manage_users')
def delete_user(user_id):
    if user_id == session['user_id']:
        return jsonify({'success': False, 'message': '不能删除自己'})
    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    return jsonify({'success': True, 'message': '用户已删除'})

@app.route('/api/product/<barcode>')
def get_product(barcode):
    product = Product.query.filter_by(barcode=barcode).first()
    if product:
        return jsonify({
            'success': True,
            'data': {
                'id': product.id,
                'name': product.name,
                'spec': product.spec,
                'unit': product.unit,
                'stock': product.stock
            }
        })
    return jsonify({'success': False, 'message': '未找到该条码对应的产品'})

@app.route('/api/inventory/search')
def search_inventory():
    keyword = request.args.get('keyword', '').strip()
    query = Product.query
    if keyword:
        pattern = f'%{keyword}%'
        query = query.filter(db.or_(Product.barcode.ilike(pattern), Product.name.ilike(pattern)))
    products = query.order_by(Product.name).all()
    data = [{
        'product_name': product.name,
        'barcode': product.barcode,
        'stock': product.stock,
        'unit': product.unit
    } for product in products]
    return jsonify({'success': True, 'data': data})

@app.route('/api/stock/in', methods=['POST'])
def stock_in():
    try:
        product_id = request.form.get('product_id')
        quantity = int(request.form.get('quantity', 1))
        operator = session.get('username', '未知')
        remark = request.form.get('remark', '')
        production_date_str = request.form.get('production_date', '')
        expiry_date_str = request.form.get('expiry_date', '')
        platform = request.form.get('platform', '')

        production_date = datetime.strptime(production_date_str, '%Y-%m-%d').date() if production_date_str else None
        expiry_date = datetime.strptime(expiry_date_str, '%Y-%m-%d').date() if expiry_date_str else None

        product = Product.query.get_or_404(product_id)
        product.stock += quantity

        record = StockRecord(
            product_id=product_id,
            type='in',
            quantity=quantity,
            operator=operator,
            remark=remark,
            production_date=production_date,
            expiry_date=expiry_date,
            platform=platform
        )
        db.session.add(record)
        db.session.commit()
        return jsonify({'success': True, 'message': '入库成功', 'current_stock': product.stock})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'入库失败：{str(e)}'})

@app.route('/api/stock/out', methods=['POST'])
def stock_out():
    try:
        product_id = request.form.get('product_id')
        quantity = int(request.form.get('quantity', 1))
        operator = session.get('username', '未知')
        remark = request.form.get('remark', '')
        waybill_number = request.form.get('waybill_number', '')
        photo_data = request.form.get('photo_data', '')
        production_date_str = request.form.get('production_date', '')
        expiry_date_str = request.form.get('expiry_date', '')
        platform = request.form.get('platform', '')

        production_date = datetime.strptime(production_date_str, '%Y-%m-%d').date() if production_date_str else None
        expiry_date = datetime.strptime(expiry_date_str, '%Y-%m-%d').date() if expiry_date_str else None

        product = Product.query.get_or_404(product_id)
        if product.stock < quantity:
            return jsonify({'success': False, 'message': f'库存不足！当前库存：{product.stock} {product.unit}'})

        photo_path = ''
        if photo_data:
            header, encoded = photo_data.split(',', 1)
            data = base64.b64decode(encoded)
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S%f')
            filename = f'out_{product.barcode}_{timestamp}.jpg'
            photo_path = os.path.join('uploads', filename)
            with open(os.path.join(app.static_folder, photo_path), 'wb') as f:
                f.write(data)

        product.stock -= quantity

        record = StockRecord(
            product_id=product_id,
            type='out',
            quantity=quantity,
            operator=operator,
            remark=remark,
            waybill_number=waybill_number,
            photo_path=photo_path,
            production_date=production_date,
            expiry_date=expiry_date,
            platform=platform
        )
        db.session.add(record)
        db.session.commit()
        return jsonify({'success': True, 'message': '出库成功', 'current_stock': product.stock})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'出库失败：{str(e)}'})

@app.route('/api/stock/out_bulk', methods=['POST'])
def stock_out_bulk():
    try:
        data = request.get_json()
        waybill = data.get('waybill_number', '').strip()
        operator = session.get('username', '未知')
        remark = data.get('remark', '')
        platform = data.get('platform', '')
        items = data.get('items', [])

        if not items:
            return jsonify({'success': False, 'message': '没有产品'})

        first_item = items[0]
        product = Product.query.get_or_404(first_item['product_id'])
        if product.stock < first_item.get('quantity', 1):
            return jsonify({'success': False, 'message': f'{product.name} 库存不足'})

        photo_path = ''
        photo_data = first_item.get('photo_data', '')
        if photo_data:
            header, encoded = photo_data.split(',', 1)
            data_bytes = base64.b64decode(encoded)
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S%f')
            filename = f'out_{product.barcode}_{timestamp}.jpg'
            photo_path = os.path.join('uploads', filename)
            with open(os.path.join(app.static_folder, photo_path), 'wb') as f:
                f.write(data_bytes)

        main_record = StockRecord(
            product_id=product.id,
            type='out',
            quantity=first_item.get('quantity', 1),
            operator=operator,
            remark=remark,
            waybill_number=waybill,
            photo_path=photo_path,
            production_date=datetime.strptime(first_item.get('production_date'), '%Y-%m-%d').date() if first_item.get('production_date') else None,
            expiry_date=datetime.strptime(first_item.get('expiry_date'), '%Y-%m-%d').date() if first_item.get('expiry_date') else None,
            platform=platform
        )
        db.session.add(main_record)
        product.stock -= first_item.get('quantity', 1)
        db.session.flush()

        for item in items[1:]:
            prod = Product.query.get_or_404(item['product_id'])
            if prod.stock < item.get('quantity', 1):
                db.session.rollback()
                return jsonify({'success': False, 'message': f'{prod.name} 库存不足'})

            sub_record = StockRecord(
                product_id=prod.id,
                type='out',
                quantity=item.get('quantity', 1),
                operator=operator,
                remark='',
                waybill_number=waybill,
                parent_id=main_record.id,
                production_date=datetime.strptime(item.get('production_date'), '%Y-%m-%d').date() if item.get('production_date') else None,
                expiry_date=datetime.strptime(item.get('expiry_date'), '%Y-%m-%d').date() if item.get('expiry_date') else None,
                platform=platform
            )
            db.session.add(sub_record)
            prod.stock -= item.get('quantity', 1)

        db.session.commit()
        return jsonify({'success': True, 'message': '批量出库成功'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'批量出库失败：{str(e)}'})

@app.route('/api/inventory/adjust', methods=['POST'])
@permission_required('adjust')
def adjust_inventory():
    try:
        product_id = request.form.get('product_id')
        quantity = int(request.form.get('quantity', 0))
        operator = session.get('username', '管理员')
        remark = request.form.get('remark', '')
        production_date_str = request.form.get('production_date', '')
        expiry_date_str = request.form.get('expiry_date', '')
        adjust_type = request.form.get('adjust_type')

        if adjust_type not in ['in', 'out']:
            return jsonify({'success': False, 'message': '操作类型错误'})
        if quantity <= 0:
            return jsonify({'success': False, 'message': '数量必须大于0'})
        production_date = datetime.strptime(production_date_str, '%Y-%m-%d').date() if production_date_str else None
        expiry_date = datetime.strptime(expiry_date_str, '%Y-%m-%d').date() if expiry_date_str else None

        product = Product.query.get_or_404(product_id)

        if adjust_type == 'out' and product.stock < quantity:
            return jsonify({'success': False, 'message': f'库存不足！当前库存：{product.stock} {product.unit}'})

        record_type = 'adjust_in' if adjust_type == 'in' else 'adjust_out'
        delta = quantity if adjust_type == 'in' else -quantity

        record = StockRecord(
            product_id=product_id,
            type=record_type,
            quantity=quantity,
            operator=operator,
            remark='库存调整：' + remark,
            production_date=production_date,
            expiry_date=expiry_date
        )
        product.stock += delta
        db.session.add(record)
        db.session.commit()

        return jsonify({'success': True, 'message': f'库存调整成功，当前库存：{product.stock}'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'调整失败：{str(e)}'})

@app.route('/api/product/delete', methods=['POST'])
@permission_required('delete')
def delete_product():
    try:
        data = request.get_json()
        product_id = data.get('product_id')
        if not product_id:
            return jsonify({'success': False, 'message': '参数不完整'})

        product = Product.query.get_or_404(product_id)

        record_ids = [record_id for (record_id,) in db.session.query(StockRecord.id).filter_by(product_id=product_id)]
        if record_ids:
            StockRecord.query.filter(StockRecord.parent_id.in_(record_ids)).update(
                {'parent_id': None}, synchronize_session=False
            )
        StockRecord.query.filter_by(product_id=product_id).delete()
        db.session.delete(product)
        db.session.commit()

        return jsonify({'success': True, 'message': f'已永久删除产品「{product.name}」及其库存记录'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'删除失败：{str(e)}'})

@app.route('/api/product/add', methods=['POST'])
def add_product():
    try:
        barcode = request.form.get('barcode')
        name = request.form.get('name')
        spec = request.form.get('spec', '')
        unit = request.form.get('unit', '个')

        if Product.query.filter_by(barcode=barcode).first():
            return jsonify({'success': False, 'message': '该条码已存在'})

        product = Product(barcode=barcode, name=name, spec=spec, unit=unit)
        db.session.add(product)
        db.session.commit()
        return jsonify({'success': True, 'message': '产品添加成功', 'product_id': product.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'添加失败：{str(e)}'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
