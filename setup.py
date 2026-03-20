import glob
from setuptools import find_packages, setup

package_name = 'video_streaming'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    package_data={
        'video_streaming': ['www/*'],
    },
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', glob.glob('launch/*.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='giangnh101',
    maintainer_email='giangnh101@todo.todo',
    description='TODO: Package description',
    license='TODO: License declaration',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'webrtc_camera_node = video_streaming.webrtc_camera_node:main',
            'camera_publisher_node = video_streaming.camera_publisher_node:main',
            'rtmp_bridge_node = video_streaming.rtmp_bridge_node:main',
        ],
    },
)
