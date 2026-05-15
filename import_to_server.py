from neo4j import GraphDatabase
from neo4j_backup import Importer

from src.neo4j_config import (
    DEFAULT_NEO4J_DATABASE,
    get_neo4j_config,
    get_neo4j_encrypted,
    require_neo4j_password,
)

if __name__ == "__main__":
    uri, username, password = get_neo4j_config()
    password = require_neo4j_password(password)
    database = DEFAULT_NEO4J_DATABASE
    encrypted = get_neo4j_encrypted(default=False)

    # 建立驱动连接
    driver = GraphDatabase.driver(uri, auth=(username, password), encrypted=encrypted)

    # 指定 data_dump 文件夹路径（如果脚本与 data_dump 同级，直接用 "./data_dump"）
    project_dir = "./data_dump"

    # 初始化导入器（移除不支持的参数）
    importer = Importer(
        project_dir=project_dir,
        driver=driver,
        database=database,
        input_yes=True          # 自动确认覆盖操作
    )

    # 执行导入
    importer.import_data()

    print("✅ 数据导入完成！")
