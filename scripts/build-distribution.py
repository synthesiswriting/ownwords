#!/usr/bin/env python3
"""Build checksum-bound Ownwords artifacts; never publish or configure a home."""
import argparse
import gzip
import io
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SOURCE = 'https://github.com/synthesiswriting/ownwords'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('artifact input must be a regular file: %s' % path)


def vendor_core(archive, expected, destination):
    regular(archive)
    if not re.fullmatch(r'[a-f0-9]{64}', expected) or sha(archive) != expected:
        raise ValueError('core archive checksum mismatch')
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        seen = set()
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] not in {'synthesis', 'package'} or not (member.isfile() or member.isdir()):
                raise ValueError('core archive contains an unsafe member')
            relative = PurePosixPath(*name.parts[1:])
            if str(relative) == '.':
                if not member.isdir():
                    raise ValueError('core archive root is not a directory')
                continue
            if relative in seen:
                raise ValueError('core archive contains a duplicate member')
            seen.add(relative)
            target = destination / str(relative)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                if member.mode & 0o7000:
                    raise ValueError('core archive has a privileged mode')
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    release = json.loads((destination / 'lib/release.json').read_text())
    if release.get('schema_version') != 1 or not re.fullmatch(r'\d+\.\d+\.\d+', str(release.get('version'))) or not re.fullmatch(r'[a-f0-9]{40}', str(release.get('commit'))):
        raise ValueError('core release identity is invalid')
    if sha(destination / 'lib/onboard.sh') != release.get('bootstrap_sha256'):
        raise ValueError('core bootstrap checksum mismatch')
    required = {'bin/synthesis', 'lib/package_launcher.py', 'lib/release_runtime.py', 'lib/onboard.sh', 'lib/release.json', 'LICENSE', 'README.md', 'package.json'}
    files = {p.relative_to(destination).as_posix(): {'sha256': sha(p), 'mode': p.stat().st_mode & 0o777}
             for p in sorted(destination.rglob('*')) if p.is_file()}
    if set(files) != required:
        raise ValueError('core thin package inventory changed; review its new contract before packaging')
    manifest = {'schema_version': 1, 'version': release['version'], 'commit': release['commit'], 'archive_sha256': expected, 'files': files}
    (destination / 'bundle-integrity.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return manifest


def archive_tree(source, destination):
    with destination.open('xb') as output, gzip.GzipFile(filename='', fileobj=output, mode='wb', mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w', format=tarfile.PAX_FORMAT) as tar:
            for item in sorted(source.rglob('*')):
                relative = item.relative_to(source).as_posix()
                if item.is_symlink():
                    # Bundled production packages may expose only internal .bin links.
                    target = item.resolve()
                    if not target.is_relative_to(source.resolve()) or not target.is_file() or '/node_modules/.bin/' not in str(item):
                        raise ValueError('distribution has an unexpected symlink')
                    continue
                if not (item.is_file() or item.is_dir()):
                    raise ValueError('distribution contains a special object')
                info = tar.gettarinfo(str(item), 'package/' + relative)
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ''
                info.mode = 0o755 if item.is_dir() or item.stat().st_mode & 0o111 else 0o644
                if item.is_file():
                    with item.open('rb') as stream:
                        tar.addfile(info, stream)
                else:
                    tar.addfile(info)


def materialize_release(source, destination):
    source=source.resolve()
    git=lambda *parts:subprocess.check_output(['git',*parts],cwd=source,text=True).strip()
    if Path(git('rev-parse','--show-toplevel')).resolve()!=source or git('status','--porcelain','--untracked-files=all'):
        raise ValueError('Ownwords packaging requires a committed clean release checkout')
    commit=git('rev-parse','HEAD^{commit}')
    version=json.loads(git('show',commit+':package.json'))['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+',version) or git('rev-parse','v'+version+'^{commit}')!=commit:
        raise ValueError('Ownwords source must equal its exact release tag')
    if destination.exists():raise ValueError('release snapshot target must be new')
    data=subprocess.check_output(['git','archive',commit],cwd=source)
    with tarfile.open(fileobj=io.BytesIO(data),mode='r:') as archive:
        members=archive.getmembers()
        for member in members:
            name=PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not name.parts or not(member.isfile() or member.isdir()):
                raise ValueError('tagged release contains an unsupported entry')
        destination.mkdir(parents=True)
        for member in members:
            target=destination/member.name
            if member.isdir():target.mkdir(parents=True,exist_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                with archive.extractfile(member) as stream:target.write_bytes(stream.read())
                target.chmod(0o755 if member.mode&0o111 else 0o644)
    return {'kind':'git','url':SOURCE,'commit':commit,'tag':'v'+version}


def build(source, output, core_archive, core_sha256, npm_cache=None, source_identity=None):
    source = source.resolve()
    output = output.absolute()
    if output == source or source in output.parents or output in source.parents:
        raise ValueError('distribution output must be outside the source checkout')
    for ancestor in (output, *output.parents):
        if ancestor.is_symlink() and ancestor not in {Path('/tmp'), Path('/var')}:
            raise ValueError('distribution output crosses a symbolic link')
    if output.exists():
        raise ValueError('output must be a new directory')
    output.mkdir(parents=True)
    package = output / 'package'
    package.mkdir()
    for name in ('bin', 'lib', 'types', 'templates', 'docs'):
        if not (source / name).exists():
            if name == 'templates':
                continue
            raise ValueError('required Ownwords package directory is missing: ' + name)
        for entry in (source / name).rglob('*'):
            if entry.is_symlink() or not (entry.is_dir() or entry.is_file()):
                raise ValueError('source package contains a link or special object')
        shutil.copytree(source / name, package / name)
    for name in ('package.json', 'package-lock.json', 'README.md', 'LICENSE'):
        regular(source / name)
        shutil.copyfile(source / name, package / name)
    core = vendor_core(core_archive, core_sha256, package / 'vendor/synthesis')
    metadata = json.loads((package / 'package.json').read_text())
    version = metadata['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Ownwords requires an exact release version')
    if set(metadata.get('scripts', {})) & {'preinstall', 'install', 'postinstall', 'prepare', 'prepublish', 'prepublishOnly', 'postpublish'}:
        raise ValueError('distribution does not permit implicit lifecycle scripts')
    # Locked dependencies are bundled so npm, Bun and direct consumers use the
    # same reviewed bytes without running installers or fetching at first use.
    command = ['npm', 'ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund']
    if npm_cache:
        command += ['--offline', '--cache', str(npm_cache)]
    subprocess.run(command, cwd=package, check=True)
    metadata['dependencies'] = {name: json.loads((package / 'node_modules' / name / 'package.json').read_text())['version'] for name in metadata.get('dependencies', {})}
    metadata['bundleDependencies'] = sorted(metadata.get('dependencies', {}))
    metadata['files'] = sorted(set(metadata['files']) | {'vendor/', 'docs/', 'package-lock.json'})
    (package / 'package.json').write_text(json.dumps(metadata, indent=2) + '\n')
    artifact = output / ('ownwords-' + version + '.tgz')
    archive_tree(package, artifact)
    checksum = sha(artifact)
    template = source / 'packages/install.sh'
    regular(template)
    installer = output / 'install.sh'
    installer.write_text(template.read_text().replace('@VERSION@', version).replace('@ARCHIVE_SHA256@', checksum))
    installer.chmod(0o755)
    installer_checksum = sha(installer)
    (output / 'SHA256SUMS').write_text(checksum + '  ' + artifact.name + '\n' + installer_checksum + '  install.sh\n')
    url = SOURCE + '/releases/download/v' + version + '/' + artifact.name
    formula = '''class Ownwords < Formula
  desc "Own your words: portable WordPress and Markdown authoring tools"
  homepage "https://github.com/synthesiswriting/ownwords"
  url "%s"
  sha256 "%s"
  license "MIT"
  depends_on "node"
  depends_on "git"
  depends_on "python@3.12"
  def install
    libexec.install Dir["*"]
    (bin/"ownwords").write_env_script libexec/"bin/ownwords.js",
      PATH: "#{Formula["node"].opt_bin}:#{ENV["PATH"]}",
      SYNTHESIS_BOOTSTRAP_PYTHON: "#{Formula["python@3.12"].opt_bin}/python3.12"
  end
  test do
    assert_match "ownwords v%s", shell_output("#{bin}/ownwords --version")
    assert_match "setup", shell_output("#{bin}/ownwords --help")
  end
end
''' % (url, checksum, version)
    (output / 'ownwords.rb').write_text(formula)
    result = {'schema_version': 1, 'version': version, 'archive': artifact.name, 'sha256': checksum,
              'url': url, 'npm_package': 'ownwords', 'brew_formula': 'synthesisengineering/tap/ownwords',
              'core': {key: core[key] for key in ('version', 'commit', 'archive_sha256')},
              'source': source_identity or {'kind':'fixture'},
              'channels': ['curl', 'npm', 'bun', 'homebrew', 'direct'], 'aur': 'not-published',
              'installer': {'file': 'install.sh', 'url': SOURCE + '/releases/download/v' + version + '/install.sh', 'sha256': installer_checksum}}
    (output / 'distribution.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--core-archive', type=Path, required=True)
    parser.add_argument('--core-sha256', required=True)
    parser.add_argument('--npm-cache', type=Path)
    args = parser.parse_args()
    source = args.repo_root.resolve()
    output=args.output.absolute()
    if output==source or source in output.parents or output in source.parents:
        raise SystemExit('distribution output must be outside the source checkout')
    with tempfile.TemporaryDirectory(prefix='ownwords-release-') as temporary:
        snapshot=Path(temporary)/'source'
        identity=materialize_release(source,snapshot)
        print(json.dumps(build(snapshot,args.output,args.core_archive,args.core_sha256,args.npm_cache,identity),indent=2))


if __name__ == '__main__':
    main()
