import glob
from setuptools import find_packages, setup

package_name = 'video_streaming'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', glob.glob('launch/*.py')),
    ],
    install_requires=['setuptools', 'boto3'],
    zip_safe=True,
    maintainer='giangnh101',
    maintainer_email='giangnh101@todo.todo',
    description='ROS 2 camera streaming via WebRTC and AWS KVS',
    license='MIT',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'camera_publisher_node = video_streaming.camera_publisher_node:main',
            'kvs_bridge_node = video_streaming.kvs_bridge_node:main',
        ],
    },
)
