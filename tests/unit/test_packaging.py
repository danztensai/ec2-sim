"""The Dockerfiles ship inside the package, so a `uv tool install` copy (what the
desktop app uses when no checkout is configured) can build every image."""

from ec2sim import config
from ec2sim.catalog import BASE_AMIS


def test_images_inside_package():
    assert config.IMAGES_DIR.parent.name == "ec2sim"
    for d in {b.image_dir for b in BASE_AMIS} | {"runner", "bastion", "forwarder"}:
        assert (config.IMAGES_DIR / d / "Dockerfile").is_file(), d
    assert (config.IMAGES_DIR / "common" / "ec2sim-init").is_file()
