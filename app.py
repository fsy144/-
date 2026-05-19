from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date
import os
import base64
import io
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment

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
    type = db.Column(db.String(10), nullable=False)  # 'in', 'out', 'adjust_in', 'adjust_out'
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
    print("✅ 数据库初始化成功！")

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

    return render_template('index.html', inventory_rows=inventory_rows)

@app.route('/records/<record_type>')
def records(record_type):
    if record_type not in ['in', 'out']:
        return redirect(url_for('index'))

    if record_type == 'in':
        records_list = StockRecord.query.filter_by(type='in').order_by(StockRecord.create_time.desc()).all()
    else:
        records_list = StockRecord.query.filter_by(type='out', parent_id=None).order_by(StockRecord.create_time.desc()).all()

    title = '入库记录' if record_type == 'in' else '出库记录'
    return render_template('records.html', records=records_list, record_type=record_type, title=title)

@app.route('/records/<record_type>/download')
def download_records(record_type):
    if record_type not in ['in', 'out']:
        return '参数错误', 400

    if record_type == 'in':
        records_list = StockRecord.query.filter_by(type='in').order_by(StockRecord.create_time.desc()).all()
        filename = '入库记录.xlsx'
    else:
        records_list = StockRecord.query.filter_by(type='out', parent_id=None).order_by(StockRecord.create_time.desc()).all()
        filename = '出库记录.xlsx'

    wb = Workbook()
    ws = wb.active
    ws.title = '记录'

    headers = ['操作时间', '产品条码', '产品名称', '规格', '数量', '操作人', '批次号', '生产日期', '到期日期', '平台', '备注']
    if record_type == 'out':
        headers.insert(6, '运单号')
    ws.append(headers)

    for rec in records_list:
        row = [
            rec.create_time.strftime('%Y-%m-%d %H:%M:%S') if rec.create_time else '',
            rec.product.barcode if rec.product else '',
            rec.product.name if rec.product else '',
            rec.product.spec if rec.product else '',
            f"{rec.quantity} {rec.product.unit if rec.product else ''}",
            rec.operator or '',
            rec.batch_no or '',
            rec.production_date.strftime('%Y-%m-%d') if rec.production_date else '',
            rec.expiry_date.strftime('%Y-%m-%d') if rec.expiry_date else '',
            rec.platform or '',
            rec.remark or ''
        ]
        if record_type == 'out':
            row.insert(6, rec.waybill_number or '')
        ws.append(row)

    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center')

    for col in ws.columns:
        max_length = 0
        col_letter = col[0].column_letter
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        ws.column_dimensions[col_letter].width = min(max_length + 2, 30)

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
    return render_template('inventory.html')

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

# 新增库存查询接口
@app.route('/api/inventory/search')
def search_inventory():
    keyword = request.args.get('keyword', '').strip()
    if not keyword:
        return jsonify({'success': False, 'message': '请输入产品条码或批次号'})

    from sqlalchemy import case, func

    # 先按条码匹配
    product = Product.query.filter_by(barcode=keyword).first()
    if product:
        batch_stocks = db.session.query(
            StockRecord.batch_no,
            func.sum(case(
                (StockRecord.type.in_(['in', 'adjust_in']), StockRecord.quantity),
                (StockRecord.type.in_(['out', 'adjust_out']), -StockRecord.quantity),
                else_=0
            )).label('net_stock')
        ).filter(
            StockRecord.product_id == product.id,
            StockRecord.batch_no.isnot(None),
            StockRecord.batch_no != ''
        ).group_by(StockRecord.batch_no).all()

        data = []
        for batch, stock in batch_stocks:
            if stock != 0:
                data.append({
                    'product_name': product.name,
                    'barcode': product.barcode,
                    'batch_no': batch,
                    'stock': stock,
                    'unit': product.unit
                })
        return jsonify({'success': True, 'data': data})

    # 按批次号匹配
    batch_stocks = db.session.query(
        StockRecord.product_id,
        StockRecord.batch_no,
        func.sum(case(
            (StockRecord.type.in_(['in', 'adjust_in']), StockRecord.quantity),
            (StockRecord.type.in_(['out', 'adjust_out']), -StockRecord.quantity),
            else_=0
        )).label('net_stock')
    ).filter(
        StockRecord.batch_no == keyword
    ).group_by(StockRecord.product_id, StockRecord.batch_no).all()

    if not batch_stocks:
        return jsonify({'success': False, 'message': '未找到匹配的库存信息'})

    data = []
    for pid, batch, stock in batch_stocks:
        if stock != 0:
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

@app.route('/api/stock/in', methods=['POST'])
def stock_in():
    try:
        product_id = request.form.get('product_id')
        quantity = int(request.form.get('quantity', 1))
        operator = request.form.get('operator', '')
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

@app.route('/api/stock/out', methods=['POST'])
def stock_out():
    try:
        product_id = request.form.get('product_id')
        quantity = int(request.form.get('quantity', 1))
        operator = request.form.get('operator', '')
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

@app.route('/api/stock/out_batch', methods=['POST'])
def stock_out_batch():
    try:
        data = request.get_json()
        waybill = data.get('waybill_number', '').strip()
        operator = data.get('operator', '')
        remark = data.get('remark', '')
        platform = data.get('platform', '')
        items = data.get('items', [])

        if not items:
            return jsonify({'success': False, 'message': '没有产品'})

        # 主记录
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
            production_date=datetime.strptime(first_item['production_date'], '%Y-%m-%d').date() if first_item.get('production_date') else None,
            expiry_date=datetime.strptime(first_item['expiry_date'], '%Y-%m-%d').date() if first_item.get('expiry_date') else None,
            platform=platform
        )
        db.session.add(main_record)
        product.stock -= first_item.get('quantity', 1)
        db.session.flush()

        # 子记录
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
                production_date=datetime.strptime(item['production_date'], '%Y-%m-%d').date() if item.get('production_date') else None,
                expiry_date=datetime.strptime(item['expiry_date'], '%Y-%m-%d').date() if item.get('expiry_date') else None,
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
def adjust_inventory():
    try:
        product_id = request.form.get('product_id')
        quantity = int(request.form.get('quantity', 0))
        operator = request.form.get('operator', '管理员')
        remark = request.form.get('remark', '')
        batch_no = request.form.get('batch_no', '')
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
            remark=remark,
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