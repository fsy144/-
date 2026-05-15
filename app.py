from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os
import base64
from werkzeug.utils import secure_filename

# 初始化Flask应用
app = Flask(__name__)
app.config.from_pyfile('config.py')

# 确保instance和uploads目录存在
os.makedirs(app.instance_path, exist_ok=True)
os.makedirs(os.path.join(app.static_folder, 'uploads'), exist_ok=True)

# 初始化数据库
db = SQLAlchemy(app)


# ------------------- 数据库模型定义 -------------------
# 产品表（核心：条码唯一）
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    barcode = db.Column(db.String(50), unique=True, nullable=False, comment='产品条码')
    name = db.Column(db.String(100), nullable=False, comment='产品名称')
    spec = db.Column(db.String(50), comment='规格型号')
    unit = db.Column(db.String(20), default='个', comment='单位')
    stock = db.Column(db.Integer, default=0, comment='当前库存')
    create_time = db.Column(db.DateTime, default=datetime.now, comment='创建时间')
    update_time = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')


# 出入库记录表
class StockRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False, comment='产品ID')
    type = db.Column(db.String(10), nullable=False, comment='操作类型：in=入库，out=出库')
    quantity = db.Column(db.Integer, nullable=False, comment='数量')
    operator = db.Column(db.String(50), comment='操作人')
    remark = db.Column(db.String(200), comment='备注')
    waybill_number = db.Column(db.String(100), comment='运单号（仅出库）')
    photo_path = db.Column(db.String(200), comment='产品照片路径（仅出库）')
    create_time = db.Column(db.DateTime, default=datetime.now, comment='操作时间')

    # 关联产品表
    product = db.relationship('Product', backref=db.backref('records', lazy=True))


# ------------------- 初始化数据库 -------------------
with app.app_context():
    db.create_all()
    print("✅ 数据库初始化成功！")


# ------------------- 路由定义 -------------------
# 首页：扫码出入库主界面
@app.route('/')
def index():
    # 获取所有产品用于库存展示
    products = Product.query.order_by(Product.create_time.desc()).all()
    return render_template('index.html', products=products)


# 出入库记录页面
@app.route('/records/<record_type>')
def records(record_type):
    if record_type not in ['in', 'out']:
        return redirect(url_for('index'))

    records = StockRecord.query.filter_by(type=record_type).order_by(StockRecord.create_time.desc()).all()
    title = '入库记录' if record_type == 'in' else '出库记录'
    return render_template('records.html', records=records, record_type=record_type, title=title)


# ------------------- API接口（扫码核心） -------------------
# 1. 根据条码查询产品
@app.route('/api/product/<barcode>')
def get_product_by_barcode(barcode):
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


# 2. 入库操作
@app.route('/api/stock/in', methods=['POST'])
def stock_in():
    try:
        product_id = request.form.get('product_id')
        quantity = int(request.form.get('quantity', 1))
        operator = request.form.get('operator', '')
        remark = request.form.get('remark', '')

        product = Product.query.get_or_404(product_id)

        # 更新库存
        product.stock += quantity

        # 记录操作
        record = StockRecord(
            product_id=product_id,
            type='in',
            quantity=quantity,
            operator=operator,
            remark=remark
        )

        db.session.add(record)
        db.session.commit()

        return jsonify({'success': True, 'message': '入库成功', 'current_stock': product.stock})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'入库失败：{str(e)}'})


# 3. 出库操作
@app.route('/api/stock/out', methods=['POST'])
def stock_out():
    try:
        product_id = request.form.get('product_id')
        quantity = int(request.form.get('quantity', 1))
        operator = request.form.get('operator', '')
        remark = request.form.get('remark', '')
        waybill_number = request.form.get('waybill_number', '')
        photo_data = request.form.get('photo_data', '')

        product = Product.query.get_or_404(product_id)

        # 检查库存
        if product.stock < quantity:
            return jsonify({'success': False, 'message': f'库存不足！当前库存：{product.stock} {product.unit}'})

        # 保存照片（如果有）
        photo_path = ''
        if photo_data:
            # 解码base64图片
            header, encoded = photo_data.split(',', 1)
            data = base64.b64decode(encoded)

            # 生成文件名
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            filename = f'out_{product.barcode}_{timestamp}.jpg'
            photo_path = os.path.join('uploads', filename)

            # 保存文件
            with open(os.path.join(app.static_folder, photo_path), 'wb') as f:
                f.write(data)

        # 更新库存
        product.stock -= quantity

        # 记录操作
        record = StockRecord(
            product_id=product_id,
            type='out',
            quantity=quantity,
            operator=operator,
            remark=remark,
            waybill_number=waybill_number,
            photo_path=photo_path
        )

        db.session.add(record)
        db.session.commit()

        return jsonify({'success': True, 'message': '出库成功', 'current_stock': product.stock})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'出库失败：{str(e)}'})


# 4. 添加新产品（首次扫码时使用）
@app.route('/api/product/add', methods=['POST'])
def add_product():
    try:
        barcode = request.form.get('barcode')
        name = request.form.get('name')
        spec = request.form.get('spec', '')
        unit = request.form.get('unit', '个')
        photo_data = request.form.get('photo_data', '')

        # 检查条码是否已存在
        if Product.query.filter_by(barcode=barcode).first():
            return jsonify({'success': False, 'message': '该条码已存在'})

        product = Product(
            barcode=barcode,
            name=name,
            spec=spec,
            unit=unit
        )

        db.session.add(product)
        db.session.commit()

        # 如果有照片，保存照片
        if photo_data:
            # 解码base64图片
            header, encoded = photo_data.split(',', 1)
            data = base64.b64decode(encoded)

            # 生成文件名
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            filename = f'product_{barcode}_{timestamp}.jpg'
            photo_path = os.path.join('uploads', filename)

            # 保存文件
            with open(os.path.join(app.static_folder, photo_path), 'wb') as f:
                f.write(data)

        return jsonify({'success': True, 'message': '产品添加成功', 'product_id': product.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'添加失败：{str(e)}'})


# ------------------- 启动应用 -------------------
if __name__ == '__main__':
    # 仅本地开发时使用，生产环境用Gunicorn启动
    app.run(host='0.0.0.0', port=5000, debug=True)