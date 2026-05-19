import os

# 项目根目录
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# 数据库配置
SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'inventory.db')
SQLALCHEMY_TRACK_MODIFICATIONS = False

# 固定密钥（请替换为你自己生成的值，不要泄露）
SECRET_KEY = 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2'

# 调试模式
DEBUG = True

# 最大上传大小（10MB）
MAX_CONTENT_LENGTH = 10 * 1024 * 1024