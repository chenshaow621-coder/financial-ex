from neo4j import GraphDatabase
from neo4j_backup import Extractor

if __name__ == "__main__":
    # --- 请修改为你的本地数据库信息 ---
    uri = "neo4j://localhost:7687"
    username = "neo4j"
    password = "123456"  # 你的密码
    # -----------------------------
    encrypted = False
    driver = GraphDatabase.driver(uri, auth=(username, password), encrypted=encrypted)

    # 创建一个目录来存放导出的数据
    project_dir = "data_dump"

    extractor = Extractor(project_dir=project_dir, driver=driver)
    extractor.extract_data()