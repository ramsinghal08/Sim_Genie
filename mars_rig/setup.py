from glob import glob

from setuptools import find_packages, setup

package_name = "mars_rig"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages",
         ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        # Installed rather than read from the source tree so nodes can be given
        # an absolute share path and no node has to know where the repo lives.
        ("share/" + package_name + "/config", glob("config/*.json")),
        ("share/" + package_name + "/launch", glob("launch/*.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Mars Rover Team, Software Subsystem",
    maintainer_email="nimayshah2022@gmail.com",
    description="Martian perception stress-test rig",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "x2_bridge_node = mars_rig.x2_bridge_node:main",
            "perception_node = mars_rig.perception_node:main",
            "scoring_node = mars_rig.scoring_node:main",
            "prompt_director = mars_rig.prompt_director:main",
            "fake_score_publisher = mars_rig.fake_score_publisher:main",
        ],
    },
)
