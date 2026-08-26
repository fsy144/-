"""基于 ip2region 离线库的 IP 归属地查询。"""
import ipaddress
import os
from pathlib import Path

try:
    from ip2region.searcher import new_with_buffer
    from ip2region.util import IPv4, load_content_from_file
except ImportError:  # 部署前尚未安装 py-ip2region 时，保留查询功能可用。
    new_with_buffer = None


XDB_PATH = os.environ.get('IP2REGION_XDB_PATH', str(Path(__file__).with_name('ip2region.xdb')))
_searcher = None
_initialised = False


def _value(parts, index):
    return parts[index] if len(parts) > index and parts[index] not in ('', '0') else None


def _get_searcher():
    global _searcher, _initialised
    if _initialised:
        return _searcher
    _initialised = True
    if new_with_buffer is None or not os.path.isfile(XDB_PATH):
        return None
    try:
        _searcher = new_with_buffer(IPv4, load_content_from_file(XDB_PATH))
    except Exception:
        _searcher = None
    return _searcher


def lookup_ip_location(ip_address):
    """返回 country / province / city；无法识别时保留为空，不中断同步。"""
    try:
        ip = ipaddress.ip_address(ip_address)
        if ip.is_private or ip.is_loopback or ip.is_reserved:
            return {'country': '内网IP', 'province': None, 'city': '本机/内网'}
        if ip.version != 4:
            return {'country': None, 'province': None, 'city': None}
    except ValueError:
        return {'country': None, 'province': None, 'city': None}

    searcher = _get_searcher()
    if searcher is None:
        return {'country': None, 'province': None, 'city': None}
    try:
        parts = searcher.search(str(ip)).split('|')
        return {
            'country': _value(parts, 1),
            'province': _value(parts, 2),
            'city': _value(parts, 3),
        }
    except Exception:
        return {'country': None, 'province': None, 'city': None}
