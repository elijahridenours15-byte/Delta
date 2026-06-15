# Demo data loader for mechanics browser
from manual_store import ManualStore
import os

def main():
    app_root = os.path.dirname(os.path.abspath(__file__))
    store = ManualStore(app_root)
    # Demo 1: Toyota Corolla 1996
    store.add_manual(
        brand="Toyota", model="Corolla", year="1996",
        title="Toyota Corolla 1996 Blueprint",
        description="Blueprint and parts for Toyota Corolla 1996 (demo)",
        license="Demo", source_url=None, pdf_path=None,
        image_paths=[
            "/static/manuals/images/demo1/blueprint.png"
        ],
        mid="demo1"
    )
    # Demo 2: Ford F-150 2015
    store.add_manual(
        brand="Ford", model="F-150", year="2015",
        title="Ford F-150 2015 Blueprint",
        description="Blueprint and parts for Ford F-150 2015 (demo)",
        license="Demo", source_url=None, pdf_path=None,
        image_paths=[
            "/static/manuals/images/demo2/blueprint.png"
        ],
        mid="demo2"
    )
    # Demo 3: Honda Civic 2010
    store.add_manual(
        brand="Honda", model="Civic", year="2010",
        title="Honda Civic 2010 Blueprint",
        description="Blueprint and parts for Honda Civic 2010 (demo)",
        license="Demo", source_url=None, pdf_path=None,
        image_paths=[
            "/static/manuals/images/demo3/blueprint.png"
        ],
        mid="demo3"
    )

if __name__ == "__main__":
    main()
