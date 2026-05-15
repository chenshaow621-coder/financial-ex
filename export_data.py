from neo4j import GraphDatabase
from neo4j_backup import Extractor

from src.neo4j_config import get_neo4j_config, get_neo4j_encrypted, require_neo4j_password

if __name__ == "__main__":
    uri, username, password = get_neo4j_config()
    password = require_neo4j_password(password)
    encrypted = get_neo4j_encrypted(default=False)
    driver = GraphDatabase.driver(uri, auth=(username, password), encrypted=encrypted)

    # 创建一个目录来存放导出的数据
    project_dir = "data_dump"

    extractor = Extractor(project_dir=project_dir, driver=driver)
    extractor.extract_data()
