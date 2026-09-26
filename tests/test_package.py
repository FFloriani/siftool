def test_package_imports_with_version():
    import siftool
    assert siftool.__version__ == "2.0.0.dev0"
