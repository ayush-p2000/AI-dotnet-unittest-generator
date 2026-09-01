import json
from testgen.scaffold import scaffold_test_project

if __name__ == "__main__":
    with open("scan_output.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)

    root = manifest["root"]
    for project in manifest["projects"]:
        test_csproj = scaffold_test_project(root, project["csproj"], project["project_name"])
        print(f"Test project ready: {test_csproj}")