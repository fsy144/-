from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date
from werkzeug.security import generate_password_hash, check_password_hash
import os
import base64
import io
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from functools import wraps

app = Flask(__name__)
app.config.from_pyfile('config.py')
app.config['SESSION_COOKIE_SECURE'] = True

os.makedirs(app.instance_path, exist_ok=True)
os.makedirs(os.path.join(app.static_folder, 'uploads'), exist_ok=True)
os.makedirs(os.path.join(app.static_folder, 'avatars'), exist_ok=True)

db = SQLAlchemy(app)

# ---------- 用户模型（增加权限字段）----------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(20), default='user')          # 'admin' 或 'user'
    permissions = db.Column(db.String(200), default='')      # 逗号分隔的权限，如 'delete,manage_users,adjust'
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

# ---------- 产品模型 ----------
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    barcode = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    spec = db.Column(db.String(50))
    unit = db.Column(db.String(20), default='个')
    stock = db.Column(db.Integer, default=0)
    create_time = db.Column(db.DateTime, default=datetime.now)
    update_time = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

# ---------- 库存记录模型 ----------
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
    batch_no = db.Column(db.String(100))
    production_date = db.Column(db.Date)
    expiry_date = db.Column(db.Date)
    platform = db.Column(db.String(50))
    create_time = db.Column(db.DateTime, default=datetime.now)

    product = db.relationship('Product', backref=db.backref('records', lazy=True))
    sub_records = db.relationship('StockRecord', backref=db.backref('parent', remote_side=[id]), lazy=True)

with app.app_context():
    db.create_all()
    # 如果没有管理员，创建默认管理员
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', role='admin', permissions='delete,manage_users,adjust')
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
    print("✅ 数据库初始化成功！")

# ---------- 装饰器 ----------
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
                # 返回友好的错误页面
                return render_template('error.html', message='您没有权限访问此页面，请联系管理员。'), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.before_request
def require_login():
    allowed_endpoints = ['login', 'static']
    if request.endpoint in allowed_endpoints:
        return
    if 'user_id' not in session:
        return redirect(url_for('login'))

# ---------- 认证路由 ----------
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

# ---------- 头像上传 ----------
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

# ---------- 工作台 ----------
@app.route('/')
def index():
    from sqlalchemy import case, func
    batch_stock_subq = db.session.query(
        StockRecord.product_id,
        StockRecord.batch_no,
        func.sum(case((StockRecord.type.in_(['in', 'adjust_in']), StockRecord.quantity),
                       (StockRecord.type.in_(['out', 'adjust_out']), -StockRecord.quantity),
                       else_=0)).label('net_stock')
    ).group_by(StockRecord.product_id, StockRecord.batch_no).subquery()

    inventory_rows = db.session.query(
        Product.id.label('product_id'),
        Product.barcode,
        Product.name,
        Product.spec,
        Product.unit,
        batch_stock_subq.c.batch_no,
        func.coalesce(batch_stock_subq.c.net_stock, 0).label('batch_stock')
    ).outerjoin(batch_stock_subq, Product.id == batch_stock_subq.c.product_id).all()

    # 获取当前用户的删除权限
    user = None
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
    can_delete = user.has_permission('delete') if user else False

    return render_template('index.html', inventory_rows=inventory_rows, can_delete=can_delete)

# ---------- 记录页面（包含调整记录）----------
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

# ---------- 下载 Excel ----------
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
        '批次号': lambda r: r.batch_no or '',
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

# ---------- 库存管理页面 ----------
@app.route('/inventory')
def inventory_page():
    return render_template('inventory.html')

# ---------- 员工管理（仅管理员）----------
@app.route('/admin/users')
@permission_required('manage_users')
def users_management():
    return render_template('users_management.html')

# API: 获取用户列表
@app.route('/api/admin/users')
@permission_required('manage_users')
def get_users():
    users = User.query.all()
    data = [{'id': u.id, 'username': u.username, 'role': u.role, 'permissions': u.permissions} for u in users]
    return jsonify({'success': True, 'users': data})

# API: 添加用户
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

# API: 更新用户权限
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

# API: 删除用户
@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@permission_required('manage_users')
def delete_user(user_id):
    if user_id == session['user_id']:
        return jsonify({'success': False, 'message': '不能删除自己'})
    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    return jsonify({'success': True, 'message': '用户已删除'})

# ---------- 产品 API ----------
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

@app.route('/api/product/<int:product_id>/batches')
def get_product_batches(product_id):
    product = Product.query.get_or_404(product_id)
    from sqlalchemy import distinct
    batches = db.session.query(distinct(StockRecord.batch_no)).filter(
        StockRecord.product_id == product_id,
        StockRecord.batch_no.isnot(None),
        StockRecord.batch_no != ''
    ).all()
    batch_list = [b[0] for b in batches]
    return jsonify({'success': True, 'batches': batch_list})

# ---------- 库存查询 API ----------
@app.route('/api/inventory/search')
def search_inventory():
    keyword = request.args.get('keyword', '').strip()
    from sqlalchemy import case, func

    net_stock_expr = func.sum(case(
        (StockRecord.type.in_(['in', 'adjust_in']), StockRecord.quantity),
        (StockRecord.type.in_(['out', 'adjust_out']), -StockRecord.quantity),
        else_=0
    ))

    if keyword:
        product = Product.query.filter_by(barcode=keyword).first()
        if product:
            batch_stocks = db.session.query(
                StockRecord.batch_no,
                net_stock_expr.label('net_stock')
            ).filter(
                StockRecord.product_id == product.id,
                StockRecord.batch_no.isnot(None),
                StockRecord.batch_no != ''
            ).group_by(StockRecord.batch_no).having(net_stock_expr != 0).all()

            data = [{
                'product_name': product.name,
                'barcode': product.barcode,
                'batch_no': batch,
                'stock': stock,
                'unit': product.unit
            } for batch, stock in batch_stocks]
            return jsonify({'success': True, 'data': data})
        else:
            batch_stocks = db.session.query(
                StockRecord.product_id,
                StockRecord.batch_no,
                net_stock_expr.label('net_stock')
            ).filter(
                StockRecord.batch_no == keyword
            ).group_by(StockRecord.product_id, StockRecord.batch_no).having(net_stock_expr != 0).all()

            data = []
            for pid, batch, stock in batch_stocks:
                prod = Product.query.get(pid)
                if prod:
                    data.append({
                        'product_name': prod.name,
                        'barcode': prod.barcode,
                        'batch_no': batch,
                        'stock': stock,
                        'unit': prod.unit
                    })
            return jsonify({'success': True, 'data': data})
    else:
        results = db.session.query(
            Product.id,
            Product.barcode,
            Product.name,
            Product.unit,
            StockRecord.batch_no,
            net_stock_expr.label('net_stock')
        ).join(StockRecord, StockRecord.product_id == Product.id)\
         .filter(StockRecord.batch_no.isnot(None), StockRecord.batch_no != '')\
         .group_by(Product.id, Product.barcode, Product.name, Product.unit, StockRecord.batch_no)\
         .having(net_stock_expr != 0).all()

        data = [{
            'product_name': name,
            'barcode': barcode,
            'batch_no': batch,
            'stock': stock,
            'unit': unit
        } for pid, barcode, name, unit, batch, stock in results]
        return jsonify({'success': True, 'data': data})

# ---------- 入库 API ----------
@app.route('/api/stock/in', methods=['POST'])
def stock_in():
    try:
        product_id = request.form.get('product_id')
        quantity = int(request.form.get('quantity', 1))
        operator = session.get('username', '未知')
        remark = request.form.get('remark', '')
        batch_no = request.form.get('batch_no', '')
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
            batch_no=batch_no,
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

# ---------- 单条出库 API ----------
@app.route('/api/stock/out', methods=['POST'])
def stock_out():
    try:
        product_id = request.form.get('product_id')
        quantity = int(request.form.get('quantity', 1))
        operator = session.get('username', '未知')
        remark = request.form.get('remark', '')
        waybill_number = request.form.get('waybill_number', '')
        photo_data = request.form.get('photo_data', '')
        batch_no = request.form.get('batch_no', '')
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
            batch_no=batch_no,
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

# ---------- 批量出库 API ----------
@app.route('/api/stock/out_batch', methods=['POST'])
def stock_out_batch():
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
            batch_no=first_item.get('batch_no', ''),
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
                batch_no=item.get('batch_no', ''),
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

# ---------- 库存调整 API ----------
@app.route('/api/inventory/adjust', methods=['POST'])
def adjust_inventory():
    try:
        product_id = request.form.get('product_id')
        quantity = int(request.form.get('quantity', 0))
        operator = session.get('username', '管理员')
        remark = request.form.get('remark', '')
        batch_no = request.form.get('batch_no', '')
        production_date_str = request.form.get('production_date', '')
        expiry_date_str = request.form.get('expiry_date', '')
        adjust_type = request.form.get('adjust_type')

        if adjust_type not in ['in', 'out']:
            return jsonify({'success': False, 'message': '操作类型错误'})
        if quantity <= 0:
            return jsonify({'success': False, 'message': '数量必须大于0'})
        if not batch_no:
            return jsonify({'success': False, 'message': '批次号不能为空'})

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
            batch_no=batch_no,
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

# ---------- 删除批次（需 delete 权限）----------
@app.route('/api/inventory/delete_batch', methods=['POST'])
@permission_required('delete')
def delete_batch_inventory():
    try:
        data = request.get_json()
        product_id = data.get('product_id')
        batch_no = data.get('batch_no', '').strip()
        operator = session.get('username', '管理员')

        if not product_id or not batch_no:
            return jsonify({'success': False, 'message': '参数不完整'})

        product = Product.query.get_or_404(product_id)

        # 删除该产品下该批次的所有记录
        StockRecord.query.filter_by(product_id=product_id, batch_no=batch_no).delete()
        # 删除产品本身
        db.session.delete(product)
        db.session.commit()

        return jsonify({'success': True, 'message': f'已永久删除产品「{product.name}」及其批次「{batch_no}」'})
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
    app.run(host='0.0.0.0', port=5000, debug=True)