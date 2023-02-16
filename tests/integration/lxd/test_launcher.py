#
# Copyright 2021-2023 Canonical Ltd.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License version 3 as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#

import io
import os
import pathlib
import subprocess
from datetime import datetime, timedelta

import pytest
from freezegun import freeze_time

from craft_providers import bases, lxd
from craft_providers.lxd import project as lxd_project

from . import conftest


@pytest.fixture()
def get_base_instance():
    def _base_instance(
        image_name: str = "20.04",
        image_remote: str = "ubuntu",
        compatibility_tag: str = "buildd-base-v0",
        project: str = "default",
    ):
        """Get the base instance."""
        base_instance_name = lxd.launcher._formulate_base_instance_name(
            image_name=image_name,
            image_remote=image_remote,
            compatibility_tag=compatibility_tag,
        )
        instance = lxd.LXDInstance(name=base_instance_name, project=project)
        return instance

    yield _base_instance


@pytest.fixture()
def core20_instance(instance_name):
    with conftest.tmp_instance(
        name=instance_name,
        image="20.04",
        image_remote="ubuntu",
        project="default",
    ):
        instance = lxd.LXDInstance(name=instance_name)

        yield instance

        if instance.exists():
            instance.delete()


@pytest.fixture()
def get_instance_and_base_instance(get_base_instance, instance_name):
    """Create and return an instance and base instance as a tuple.

    Delete instances on fixture teardown.
    """
    base_instance = get_base_instance()
    base_configuration = bases.BuilddBase(alias=bases.BuilddBaseAlias.FOCAL)

    # launch an instance from an image and create a base instance
    instance = lxd.launch(
        name=instance_name,
        base_configuration=base_configuration,
        image_name="20.04",
        image_remote="ubuntu",
        use_base_instance=True,
    )
    try:
        # the instance and base instance should both exist
        assert instance.exists()
        assert base_instance.exists()

        # only the instance should be running
        assert instance.is_running()
        assert not base_instance.is_running()
        yield (instance, base_instance)
    finally:
        if instance.exists():
            instance.delete()
        if base_instance.exists():
            base_instance.delete()


@pytest.mark.parametrize(
    "alias,image_name",
    [
        (bases.BuilddBaseAlias.XENIAL, "16.04"),
        (bases.BuilddBaseAlias.BIONIC, "18.04"),
        (bases.BuilddBaseAlias.FOCAL, "20.04"),
        (bases.BuilddBaseAlias.JAMMY, "22.04"),
    ],
)
def test_launch_and_run(instance_name, alias, image_name):
    """Launch an instance and run a command in the instance."""
    base_configuration = bases.BuilddBase(alias=alias)

    instance = lxd.launch(
        name=instance_name,
        base_configuration=base_configuration,
        image_name=image_name,
        image_remote="ubuntu",
    )

    try:
        assert isinstance(instance, lxd.LXDInstance)
        assert instance.exists()
        assert instance.is_running()

        proc = instance.execute_run(["echo", "hi"], check=True, stdout=subprocess.PIPE)

        assert proc.stdout == b"hi\n"
    finally:
        instance.delete()


def test_launch_use_snapshots_deprecated(get_base_instance, instance_name):
    """Launch an instance with the deprecated parameter `use_snapshots`."""
    base_instance = get_base_instance()
    base_configuration = bases.BuilddBase(alias=bases.BuilddBaseAlias.FOCAL)

    instance = lxd.launch(
        name=instance_name,
        base_configuration=base_configuration,
        image_name="20.04",
        image_remote="ubuntu",
        use_snapshots=True,
    )
    try:
        # verify the instance and base instance exist
        assert instance.exists()
        assert base_instance.exists()
    finally:
        if instance.exists():
            instance.delete()
        if base_instance.exists():
            base_instance.delete()


def test_launch_use_base_instance(get_instance_and_base_instance, instance_name):
    """Launch an instance using base instances.

    First, launch an instance from an image and create a base instance.
    Then launch an instance from the base instance.
    Then launch an instance when the instance exists.

    The parameter `use_base_instance` and the deprecated parameter `use_snapshots`
    should both result in the same behavior.
    """
    base_configuration = bases.BuilddBase(alias=bases.BuilddBaseAlias.FOCAL)
    instance, base_instance = get_instance_and_base_instance

    # fingerprint the base instance
    base_instance.start()
    base_instance.execute_run(["touch", "/base-instance"])
    base_instance.stop()

    # delete the instance so a new instance is created from the base instance
    instance.delete()
    instance = lxd.launch(
        name=instance_name,
        base_configuration=base_configuration,
        image_name="20.04",
        image_remote="ubuntu",
        use_base_instance=True,
    )

    assert instance.exists()
    assert instance.is_running()

    # confirm instance was created from the base instance by checking fingerprint
    instance.execute_run(["stat", "/base-instance"], check=True)

    # fingerprint the instance
    instance.execute_run(["touch", "/instance"])

    # relaunch the existing instance
    instance = lxd.launch(
        name=instance_name,
        base_configuration=base_configuration,
        image_name="20.04",
        image_remote="ubuntu",
        use_base_instance=True,
    )

    # confirm the same instance was launched with both fingerprints
    instance.execute_run(["stat", "/base-instance"], check=True)
    instance.execute_run(["stat", "/instance"], check=True)

    assert instance.exists()
    assert instance.is_running()


@freeze_time(datetime.now() + timedelta(days=91))
def test_launch_use_base_instance_expired(
    get_instance_and_base_instance, instance_name
):
    """Launch an instance using an expired base instance.

    First, launch an instance from an image and create a base instance.
    Then launch an instance from the expired base instance, which will trigger the
    creation of a new instance and base instance.

    The LXD instance is created via subprocess, so the creation date the instance is
    out of freezegun's scope and can't be modified.
    """
    base_configuration = bases.BuilddBase(alias=bases.BuilddBaseAlias.FOCAL)
    instance, base_instance = get_instance_and_base_instance

    # fingerprint the expired base instance
    base_instance.start()
    base_instance.execute_run(["touch", "/base-instance"])
    base_instance.stop()

    # delete the instance so a new instance is created from the base instance
    instance.delete()
    instance = lxd.launch(
        name=instance_name,
        base_configuration=base_configuration,
        image_name="20.04",
        image_remote="ubuntu",
        use_base_instance=True,
    )

    assert instance.exists()
    assert instance.is_running()

    # confirm instance does not have the expired base instance's fingerprint
    proc = instance.execute_run(
        ["stat", "/base-instance"], capture_output=True, text=True
    )
    assert proc.returncode == 1
    assert "'/base-instance': No such file or directory" in proc.stderr

    # confirm new base instance does not have the expired base instance's fingerprint
    base_instance.start()
    proc = base_instance.execute_run(
        ["stat", "/base-instance"], capture_output=True, text=True
    )
    assert proc.returncode == 1
    assert "'/base-instance': No such file or directory" in proc.stderr


def test_launch_create_project(instance_name, project_name):
    """Create a project if it does not exist and `auto_create_project` is true."""
    base_configuration = bases.BuilddBase(alias=bases.BuilddBaseAlias.FOCAL)
    lxc = lxd.LXC()

    assert project_name not in lxc.project_list()

    try:
        instance = lxd.launch(
            name=instance_name,
            base_configuration=base_configuration,
            image_name="20.04",
            image_remote="ubuntu",
            auto_create_project=True,
            project=project_name,
            remote="local",
        )

        assert instance.exists()
        assert project_name in lxc.project_list()
    finally:
        lxd_project.purge(lxc=lxc, project=project_name)


def test_launch_with_project_and_use_base_instance(
    get_base_instance, instance_name, project
):
    """With a LXD project specified, launch an instance and use base instances."""
    base_instance = get_base_instance(project=project)
    base_configuration = bases.BuilddBase(alias=bases.BuilddBaseAlias.FOCAL)

    # launch an instance from an image and create a base instance
    instance = lxd.launch(
        name=instance_name,
        base_configuration=base_configuration,
        image_name="20.04",
        image_remote="ubuntu",
        use_base_instance=True,
        project=project,
        remote="local",
    )

    try:
        # the instance and base instance should both exist
        assert instance.exists()
        assert base_instance.exists()

        # only the instance should be running
        assert instance.is_running()
        assert not base_instance.is_running()

        # delete the instance so a new instance is created from the base instance
        instance.delete()
        instance = lxd.launch(
            name=instance_name,
            base_configuration=base_configuration,
            image_name="20.04",
            image_remote="ubuntu",
            use_base_instance=True,
            project=project,
            remote="local",
        )

        assert instance.exists()
        assert instance.is_running()

        # relaunch the existing instance
        instance = lxd.launch(
            name=instance_name,
            base_configuration=base_configuration,
            image_name="20.04",
            image_remote="ubuntu",
            use_base_instance=True,
            project=project,
            remote="local",
        )

        assert instance.exists()
        assert instance.is_running()
    finally:
        if instance.exists():
            instance.delete()
        if base_instance.exists():
            base_instance.delete()


def test_launch_ephemeral(instance_name):
    """Launch an ephemeral instance and verify it is deleted after it is stopped."""
    base_configuration = bases.BuilddBase(alias=bases.BuilddBaseAlias.FOCAL)

    instance = lxd.launch(
        name=instance_name,
        base_configuration=base_configuration,
        image_name="20.04",
        image_remote="ubuntu",
        ephemeral=True,
    )

    try:
        # lxd will delete the instance when it is stopped
        instance.stop()

        assert not instance.exists()
    finally:
        if instance.exists():
            instance.delete()


def test_launch_ephemeral_existing(instance_name):
    """If an ephemeral instance already exists, delete it and create a new instance."""
    base_configuration = bases.BuilddBase(alias=bases.BuilddBaseAlias.FOCAL)

    # create a non-ephemeral instance
    instance = lxd.launch(
        name=instance_name,
        base_configuration=base_configuration,
        image_name="20.04",
        image_remote="ubuntu",
        ephemeral=False,
    )

    try:
        assert instance.exists()
        assert instance.is_running()

        # relaunching as an ephemeral instance will delete the existing instance
        instance = lxd.launch(
            name=instance_name,
            base_configuration=base_configuration,
            image_name="20.04",
            image_remote="ubuntu",
            ephemeral=True,
        )

        assert instance.exists()

        # lxd will delete the instance when it is stopped
        instance.stop()

        assert not instance.exists()
    finally:
        if instance.exists():
            instance.delete()


def test_launch_map_user_uid_true(instance_name, tmp_path):
    """Enable and map the the UID of the test account."""
    tmp_path.chmod(0o755)

    base_configuration = bases.BuilddBase(alias=bases.BuilddBaseAlias.FOCAL)

    instance = lxd.launch(
        name=instance_name,
        base_configuration=base_configuration,
        image_name="20.04",
        image_remote="ubuntu",
        map_user_uid=True,
        uid=os.stat(tmp_path).st_uid,
    )

    try:
        instance.mount(host_source=tmp_path, target=pathlib.Path("/mnt"))

        # If user ID mappings are enabled, we will be able to write.
        instance.execute_run(["touch", "/mnt/foo"], capture_output=True, check=True)
    finally:
        if instance.exists():
            instance.delete()


def test_launch_map_user_uid_true_no_uid(instance_name, tmp_path):
    """Enable UID mapping without specifying a UID."""
    tmp_path.chmod(0o755)

    base_configuration = bases.BuilddBase(alias=bases.BuilddBaseAlias.FOCAL)

    instance = lxd.launch(
        name=instance_name,
        base_configuration=base_configuration,
        image_name="20.04",
        image_remote="ubuntu",
        map_user_uid=True,
    )

    try:
        instance.mount(host_source=tmp_path, target=pathlib.Path("/mnt"))

        # If user ID mappings are enabled, we will be able to write.
        instance.execute_run(["touch", "/mnt/foo"], capture_output=True, check=True)
    finally:
        if instance.exists():
            instance.delete()


def test_launch_map_user_uid_false(instance_name, tmp_path):
    """If UID mapping is not enabled, access to a mounted directory will be denied."""
    tmp_path.chmod(0o755)

    base_configuration = bases.BuilddBase(alias=bases.BuilddBaseAlias.FOCAL)

    instance = lxd.launch(
        name=instance_name,
        base_configuration=base_configuration,
        image_name="20.04",
        image_remote="ubuntu",
        map_user_uid=False,
    )

    try:
        instance.mount(host_source=tmp_path, target=pathlib.Path("/mnt"))

        # If user ID mappings are not enabled, we won't be able to write.
        with pytest.raises(subprocess.CalledProcessError):
            instance.execute_run(["touch", "/mnt/foo"], capture_output=True, check=True)
    finally:
        if instance.exists():
            instance.delete()


def test_launch_existing_instance(core20_instance):
    """Launch an existing instance and run a command."""
    base_configuration = bases.BuilddBase(alias=bases.BuilddBaseAlias.FOCAL)

    instance = lxd.launch(
        name=core20_instance.name,
        base_configuration=base_configuration,
        image_name="20.04",
        image_remote="ubuntu",
    )

    assert instance.exists()
    assert instance.is_running()

    proc = instance.execute_run(["echo", "hi"], check=True, stdout=subprocess.PIPE)

    assert proc.stdout == b"hi\n"


def test_launch_os_incompatible_without_auto_clean(core20_instance):
    """Raise an error if the OS is incompatible and auto_clean is False."""
    base_configuration = bases.BuilddBase(alias=bases.BuilddBaseAlias.FOCAL)

    core20_instance.push_file_io(
        destination=pathlib.Path("/etc/os-release"),
        content=io.BytesIO(b"NAME=Fedora\nVERSION_ID=32\n"),
        file_mode="0644",
    )

    # will raise compatibility error when auto_clean is false
    with pytest.raises(bases.BaseCompatibilityError) as exc_info:
        lxd.launch(
            name=core20_instance.name,
            base_configuration=base_configuration,
            image_name="20.04",
            image_remote="ubuntu",
        )

    assert (
        exc_info.value.brief
        == "Incompatible base detected: Expected OS 'Ubuntu', found 'Fedora'."
    )


def test_launch_os_incompatible_with_auto_clean(core20_instance):
    """Clean the instance if the OS is incompatible and auto_clean is True."""
    base_configuration = bases.BuilddBase(alias=bases.BuilddBaseAlias.FOCAL)

    core20_instance.push_file_io(
        destination=pathlib.Path("/etc/os-release"),
        content=io.BytesIO(b"NAME=Fedora\nVERSION_ID=32\n"),
        file_mode="0644",
    )

    # when auto_clean is true, the instance will be deleted and recreated
    lxd.launch(
        name=core20_instance.name,
        base_configuration=base_configuration,
        image_name="20.04",
        image_remote="ubuntu",
        auto_clean=True,
    )

    assert core20_instance.exists()
    assert core20_instance.is_running()


def test_launch_instance_config_incompatible_without_auto_clean(core20_instance):
    """Raise an error if the config file is incompatible and auto_clean is False."""
    base_configuration = bases.BuilddBase(alias=bases.BuilddBaseAlias.FOCAL)

    core20_instance.push_file_io(
        destination=base_configuration.instance_config_path,
        content=io.BytesIO(b"compatibility_tag: invalid\n"),
        file_mode="0644",
    )

    # will raise compatibility error when auto_clean is false
    with pytest.raises(bases.BaseCompatibilityError) as exc_info:
        lxd.launch(
            name=core20_instance.name,
            base_configuration=base_configuration,
            image_name="20.04",
            image_remote="ubuntu",
        )

    assert exc_info.value.brief == (
        "Incompatible base detected:"
        " Expected image compatibility tag 'buildd-base-v0', found 'invalid'."
    )


def test_launch_instance_config_incompatible_with_auto_clean(core20_instance):
    """Clean the instance if the config is incompatible and auto_clean is True."""
    base_configuration = bases.BuilddBase(alias=bases.BuilddBaseAlias.FOCAL)

    core20_instance.push_file_io(
        destination=base_configuration.instance_config_path,
        content=io.BytesIO(b"compatibility_tag: invalid\n"),
        file_mode="0644",
    )

    # when auto_clean is true, the instance will be deleted and recreated
    lxd.launch(
        name=core20_instance.name,
        base_configuration=base_configuration,
        image_name="20.04",
        image_remote="ubuntu",
        auto_clean=True,
    )

    assert core20_instance.exists()
    assert core20_instance.is_running()


def test_launch_instance_id_map_incompatible_without_auto_clean(core20_instance):
    """Raise an error if the id map is incompatible and auto_clean is False."""
    base_configuration = bases.BuilddBase(alias=bases.BuilddBaseAlias.FOCAL)

    lxc = lxd.LXC()
    lxc.config_set(
        instance_name=core20_instance.instance_name,
        key="raw.idmap",
        value="",  # equivalent to `lxc config unset`, which is not yet implemented
    )

    # will raise compatibility error when auto_clean is false
    with pytest.raises(bases.BaseCompatibilityError) as exc_info:
        lxd.launch(
            name=core20_instance.name,
            base_configuration=base_configuration,
            image_name="20.04",
            image_remote="ubuntu",
            map_user_uid=True,
        )

    assert exc_info.value.brief == (
        "Incompatible base detected: "
        "the instance's id map ('raw.idmap') is not configured as expected."
    )


def test_launch_instance_id_map_incompatible_with_auto_clean(core20_instance):
    """Clean the instance if the id map is incompatible and auto_clean is True."""
    base_configuration = bases.BuilddBase(alias=bases.BuilddBaseAlias.FOCAL)

    lxc = lxd.LXC()
    lxc.config_set(
        instance_name=core20_instance.instance_name,
        key="raw.idmap",
        value="",  # equivalent to `lxc config unset`, which is not yet implemented
    )

    # when auto_clean is true, the instance will be deleted and recreated
    lxd.launch(
        name=core20_instance.name,
        base_configuration=base_configuration,
        image_name="20.04",
        image_remote="ubuntu",
        auto_clean=True,
    )

    assert core20_instance.exists()
    assert core20_instance.is_running()
