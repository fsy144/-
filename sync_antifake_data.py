"""只读导入 .com 防伪数据库的历史数据及增量扫码记录。

在 phatma.cn 服务器运行：
    /var/www/phatma.cn/xuni/bin/python3 sync_antifake_data.py

可用 ANTI_FAKE_SOURCE_DB 环境变量覆盖来源库路径。
本脚本从不对来源 anti_fake.db 执行写入操作。
"""
import os
import sqlite3
import sys
from datetime import datetime

from app import app, db, AntiFakeCode, AntiFakeScanEvent, AntiFakeSyncState
from ip_location import lookup_ip_location


SOURCE_DB_PATH = os.environ.get(
    'ANTI_FAKE_SOURCE_DB',
    '/var/www/pharmanewzealand.com/database/anti_fake.db'
)
BATCH_SIZE = 1000


def parse_scan_time(value):
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return datetime.now()


def source_connection():
    if not os.path.isfile(SOURCE_DB_PATH):
        raise FileNotFoundError(f'未找到 .com 防伪数据库: {SOURCE_DB_PATH}')
    # mode=ro 保证 SQLite 连接只能读取，避免任何意外写入来源库。
    return sqlite3.connect(f'file:{SOURCE_DB_PATH}?mode=ro', uri=True)


def sync_codes(source, state):
    imported = 0
    cursor = source.execute(
        '''SELECT id, qr_id, serial_no, scan_count, created_at
           FROM anti_fake_records WHERE id > ? ORDER BY id''',
        (state.last_code_id,)
    )
    while rows := cursor.fetchmany(BATCH_SIZE):
        qr_ids = [row[1] for row in rows]
        existing = {
            code.qr_id: code for code in AntiFakeCode.query.filter(AntiFakeCode.qr_id.in_(qr_ids)).all()
        }
        for source_id, qr_id, serial_no, scan_count, created_at in rows:
            code = existing.get(qr_id)
            if code is None:
                code = AntiFakeCode(qr_id=qr_id, serial_no=serial_no)
                db.session.add(code)
            code.scan_count = scan_count or 0
            state.last_code_id = source_id
            imported += 1
        db.session.commit()
    return imported


def sync_events(source, state):
    imported = 0
    cursor = source.execute(
        '''SELECT id, qr_id, ip_address, platform, scan_time
           FROM scan_logs WHERE id > ? ORDER BY id''',
        (state.last_scan_log_id,)
    )
    while rows := cursor.fetchmany(BATCH_SIZE):
        for source_id, qr_id, ip_address, platform, scan_time in rows:
            event_id = f'legacy:{source_id}'
            # 唯一事件 ID 使脚本即使被重复执行也不会重复导入。
            if not AntiFakeScanEvent.query.filter_by(source_event_id=event_id).first():
                location = lookup_ip_location(ip_address)
                db.session.add(AntiFakeScanEvent(
                    source_event_id=event_id,
                    qr_id=qr_id,
                    ip_address=ip_address,
                    platform=platform,
                    scan_channel='二维码扫码',
                    verification_result='success',
                    scan_time=parse_scan_time(scan_time),
                    **location,
                ))
                imported += 1
            state.last_scan_log_id = source_id
        db.session.commit()
    return imported


def enrich_existing_events(force=False):
    """为已导入但尚无归属地的历史扫码记录补充省市信息。"""
    enriched = 0
    last_id = 0
    while True:
        query = AntiFakeScanEvent.query.filter(AntiFakeScanEvent.id > last_id)
        if not force:
            query = query.filter(
                db.or_(AntiFakeScanEvent.country.is_(None), AntiFakeScanEvent.country == '')
            )
        events = query.order_by(AntiFakeScanEvent.id).limit(BATCH_SIZE).all()
        if not events:
            break
        for event in events:
            location = lookup_ip_location(event.ip_address)
            last_id = event.id
            if any(location.values()):
                event.country = location['country']
                event.province = location['province']
                event.city = location['city']
                enriched += 1
        db.session.commit()
    return enriched


def main():
    with app.app_context():
        state = AntiFakeSyncState.query.filter_by(source_name='pharmanewzealand.com').first()
        if state is None:
            state = AntiFakeSyncState(source_name='pharmanewzealand.com')
            db.session.add(state)
            db.session.commit()

        with source_connection() as source:
            code_count = sync_codes(source, state)
            event_count = sync_events(source, state)
        location_count = enrich_existing_events(force='--refresh-locations' in sys.argv)
        print(
            f'同步完成：防伪码 {code_count} 条，扫码记录 {event_count} 条，'
            f'归属地补全 {location_count} 条。'
        )


if __name__ == '__main__':
    main()
