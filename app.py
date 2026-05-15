from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os
import base64

app = Flask(__name__)
app.config.from_pyfile('config.py')

os.makedirs(app.instance_path, exist_ok=True)
os.makedirs(os.path.join(app.static_folder, 'uploads'), exist_ok=True)

db = SQLAlchemy(app)

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
    create_time = db.Column(db.DateTime, default=datetime.now)

    product = db.relationship('Product', backref=db.backref('records', lazy=True))
    sub_records = db.relationship('StockRecord', backref=db.backref('parent', remote_side=[id]), lazy=True)

with app.app_context():
    db.create_all()
    print("✅ 数据库初始化成功！")

@app.route('/')
def index():
    products = Product.query.order_by(Product.create_time.desc()).all()
    return render_template('index.html', products=products)

@app.route('/records/<record_type>')
def records(record_type):
    if record_type not in ['in', 'out']:
        return redirect(url_for('index'))

    if record_type == 'in':
        records_list = StockRecord.query.filter_by(type='in').order_by(StockRecord.create_time.desc()).all()
    else:
        # 只取主记录（parent_id 为 NULL）
        records_list = StockRecord.query.filter_by(type='out', parent_id=None).order_by(StockRecord.create_time.desc()).all()

    title = '入库记录' if record_type == 'in' else '出库记录'
    return render_template('records.html', records=records_list, record_type=record_type, title=title)

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

@app.route('/api/stock/in', methods=['POST'])
def stock_in():
    try:
        product_id = request.form.get('product_id')
        quantity = int(request.form.get('quantity', 1))
        operator = request.form.get('operator', '')
        remark = request.form.get('remark', '')

        product = Product.query.get_or_404(product_id)
        product.stock += quantity

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
            photo_path=photo_path
        )
        db.session.add(record)
        db.session.commit()
        return jsonify({'success': True, 'message': '出库成功', 'current_stock': product.stock})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'出库失败：{str(e)}'})

@app.route('/api/stock/out_batch', methods=['POST'])
def stock_out_batch():
    try:
        data = request.get_json()
        waybill = data.get('waybill_number', '').strip()
        operator = data.get('operator', '')
        remark = data.get('remark', '')
        items = data.get('items', [])

        if not items:
            return jsonify({'success': False, 'message': '没有产品'})

        # 创建主记录（第一个产品）
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
            photo_path=photo_path
        )
        db.session.add(main_record)
        product.stock -= first_item.get('quantity', 1)

        # 处理剩余子记录
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
                parent_id=None  # 先保存主记录后获取 id
            )
            db.session.add(sub_record)
            prod.stock -= item.get('quantity', 1)

        db.session.flush()  # 获取 main_record.id

        # 更新子记录的 parent_id
        for sub in StockRecord.query.filter(StockRecord.parent_id == None, StockRecord.id != main_record.id).all():
            sub.parent_id = main_record.id

        db.session.commit()
        return jsonify({'success': True, 'message': '批量出库成功'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'批量出库失败：{str(e)}'})

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