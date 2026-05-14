# 基础配置
import os
import secrets

# 项目根目录
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# 数据库配置
SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'inventory.db')
SQLALCHEMY_TRACK_MODIFICATIONS = False

# 密钥（用于后续扩展登录功能）
SECRET_KEY = secrets.token_hex(32)

# 调试模式
DEBUG = False