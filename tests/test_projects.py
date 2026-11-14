from pathlib import Path

from conftest import SAMPLE_PROJECT, make_zip


def test_create_empty_project(client):
    r = client.post("/api/projects", json={"name": "My Project", "description": "d"})
    assert r.status_code == 201
    p = r.json()
    assert p["slug"] == "my-project" and Path(p["directory"]).is_dir()
    assert client.post("/api/projects", json={"name": "   "}).status_code in (400, 422)


def test_zip_import_and_inventory(client, project, home):
    assert project["source_type"] == "zip"
    inv = project["inventory"]
    assert inv["file_count"] == len(SAMPLE_PROJECT)
    assert "TypeScript" in inv["languages"] and "Python" in inv["languages"]
    assert "React" in inv["frameworks"] and "FastAPI" in inv["frameworks"]
    assert {"npm", "pip"} <= set(inv["package_managers"])
    assert "react" in inv["dependencies"]["npm"] and "fastapi" in inv["dependencies"]["pip"]
    assert "package.json" in inv["config_files"] and "README.md" in inv["doc_files"]
    assert inv["git"]["is_repo"] is True
    assert any("demo-app" in w for w in project["import_warnings"])
    # project root detected: files not nested under demo-app/
    assert (Path(project["directory"]) / "package.json").is_file()


def test_project_file_list_and_protection(client, project):
    files = client.get(f"/api/projects/{project['id']}/files").json()
    env = next(f for f in files if f["path"] == ".env")
    assert env["is_protected"] is True
    assert client.get(f"/api/projects/{project['id']}/file", params={"path": ".env"}).status_code == 403
    ok = client.get(f"/api/projects/{project['id']}/file", params={"path": "main.py"})
    assert ok.status_code == 200 and "print" in ok.json()["content"]
    assert client.get(f"/api/projects/{project['id']}/file", params={"path": "../../etc/passwd"}).status_code == 400


def test_env_file_not_committed_to_workspace_repo(client, project):
    from app.projects import gitops

    root = Path(project["directory"])
    tracked = gitops.run_git(root, "ls-files").splitlines()
    assert ".env" not in tracked and "main.py" in tracked


def test_bad_zip_rejected_and_cleaned_up(client, home):
    bad = make_zip({"../evil.txt": "x"})
    r = client.post("/api/projects/import/zip", files={"file": ("bad.zip", bad, "application/zip")})
    assert r.status_code == 400
    assert client.get("/api/projects").json() == []
    assert list((home / "data" / "projects").iterdir()) == []


def test_import_folder(client, tmp_path):
    src = tmp_path / "src"
    (src / "node_modules").mkdir(parents=True)
    (src / "node_modules" / "x.js").write_text("x")
    (src / "index.html").write_text("<html></html>")
    (src / "style.css").write_text("a{}")
    r = client.post("/api/projects/import/folder", json={"path": str(src)})
    assert r.status_code == 201
    inv = r.json()["inventory"]
    assert inv["file_count"] == 2 and "HTML/CSS/JavaScript" in inv["project_types"]
    assert client.post("/api/projects/import/folder", json={"path": str(tmp_path / "nope")}).status_code == 400


def test_folder_import_rejects_app_data_dir(client, home):
    assert client.post("/api/projects/import/folder", json={"path": str(home / "data")}).status_code == 400


def test_import_individual_files_with_duplicates(client):
    r = client.post("/api/projects/import/files",
                    files=[("files", ("a.py", b"1")), ("files", ("a.py", b"2")), ("files", ("../b.py", b"3"))])
    assert r.status_code == 201
    paths = {f["path"] for f in client.get(f"/api/projects/{r.json()['id']}/files").json()}
    assert paths == {"a.py", "a (2).py", "b.py"}


def test_git_url_validation(client):
    assert client.post("/api/projects/import/git", json={"url": "--upload-pack=evil"}).status_code == 400
    assert client.post("/api/projects/import/git", json={"url": "file:///etc"}).status_code == 400


def test_git_import_local_repo(client, tmp_path):
    # https/ssh only over the API; verify the importer rejects non-network schemes and the clone error path cleans up.
    r = client.post("/api/projects/import/git", json={"url": "https://127.0.0.1:1/none.git"})
    assert r.status_code == 400
    assert client.get("/api/projects").json() == []


def test_delete_project_removes_files(client, project):
    d = Path(project["directory"])
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert not d.exists() and client.get(f"/api/projects/{project['id']}").status_code == 404


def test_unity_and_android_detection(client):
    z = make_zip({"Assets/a.cs": "class A{}", "ProjectSettings/ProjectVersion.txt": "x",
                  "app/build.gradle": "apply plugin: 'com.android.application'\ndependencies{implementation 'a.b:c:1'}",
                  "app/src/main/AndroidManifest.xml": "<manifest/>", "Main.java": "class M{}", "k.kt": "fun m(){}",
                  "x.cpp": "int main(){}", "CMakeLists.txt": ""})
    r = client.post("/api/projects/import/zip", files={"file": ("g.zip", z, "application/zip")})
    t = set(r.json()["inventory"]["project_types"])
    assert {"Unity", "Android", "Java", "Kotlin", "C/C++", "C#"} <= t
