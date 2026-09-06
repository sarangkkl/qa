"""`qa vault ...` - the commands that manage stored credentials.

There is deliberately no `get`, and `set` never takes the value as an argument: a value
leaves the keychain only into a running browser, and never into shell history.
"""

from typing import Any

from nkqa.shell.commands import Command, Param, ShellContext
from nkqa.vault import ENV_PREFIX, Vault, normalize


def _vault(ctx: ShellContext) -> Vault:
	return ctx.hitl.vault or Vault(ctx.ws)


async def _status(ctx: ShellContext, a: dict[str, Any]) -> int:
	vault = _vault(ctx)
	rows = vault.status()
	backend = vault.keychain.label if vault.keychain else 'none (environment variables only)'
	await ctx.ch.log(f'\nVault backend: {backend} · entry: {vault.service}')
	if not rows:
		await ctx.ch.log(f'\nNo credentials declared. List what this project needs in {ctx.ws.vault_file.name},')
		await ctx.ch.log('then store each one:  qa vault set <name>')
		return 0
	await ctx.ch.log(f'\n{"NAME":<20} {"STORED":<16} {"GRANT":<14} ORIGIN')
	missing = 0
	for row in rows:
		if not row['stored']:
			missing += 1
		await ctx.ch.log(
			f'{row["name"]:<20} {row["stored"] or "— not set":<16} {row["grant"] or "—":<14} {row["origin"]}'
		)
	if missing:
		await ctx.ch.log(f'\n{missing} credential(s) not set yet:  qa vault set <name>')
	else:
		await ctx.ch.log('\nAll declared credentials are set.')
	return 1 if missing else 0


async def _set(ctx: ShellContext, a: dict[str, Any]) -> int:
	vault = _vault(ctx)
	name = normalize(str(a.get('name', '')))
	if not name:
		await ctx.ch.log('Which credential?  qa vault set <name>')
		return 2
	if not vault.writable:
		await ctx.ch.log(f'No OS keychain on this machine. Set {ENV_PREFIX}{name.upper()} in the environment instead.')
		return 2
	if name not in vault.specs:
		await ctx.ch.log(f'⚠️  "{name}" is not declared in {ctx.ws.vault_file.name} - storing it anyway, but declare it')
		await ctx.ch.log('   so the project stays self-describing (and so it can be bound to an origin).')
	value = await ctx.ch.ask_secret(name, f'🔑 Value for "{name}" (hidden, never echoed): ')
	if not value:
		await ctx.ch.log('Nothing stored.')
		return 1
	vault.set(name, value)
	await ctx.ch.log(f'🔐 Stored "{name}" in the {vault.keychain.label if vault.keychain else "keychain"}.')
	return 0


async def _rm(ctx: ShellContext, a: dict[str, Any]) -> int:
	vault = _vault(ctx)
	name = normalize(str(a.get('name', '')))
	value, source = vault.get(name)
	if not value:
		await ctx.ch.log(f'"{name}" is not stored.')
		return 2
	if source == 'environment':
		await ctx.ch.log(f'"{name}" comes from {ENV_PREFIX}{name.upper()} - unset it in your environment.')
		return 2
	vault.delete(name)
	vault.revoke(name)
	await ctx.ch.log(f'🧹 Removed "{name}" and its grant.')
	return 0


async def _grant(ctx: ShellContext, a: dict[str, Any]) -> int:
	vault = _vault(ctx)
	name = normalize(str(a.get('name', '')))
	scenario = str(a.get('scenario', ''))
	if name not in vault.specs:
		await ctx.ch.log(f'"{name}" is not declared in {ctx.ws.vault_file.name}.')
		return 2
	vault.grant(name, scenario)
	where = f'scenario {scenario}' if scenario else 'any scenario'
	await ctx.ch.log(f'✅ "{name}" may be used for {where} without asking. Revoke:  qa vault revoke {name}')
	return 0


async def _revoke(ctx: ShellContext, a: dict[str, Any]) -> int:
	vault = _vault(ctx)
	name = normalize(str(a.get('name', '')))
	if not vault.revoke(name):
		await ctx.ch.log(f'"{name}" had no grant.')
		return 2
	await ctx.ch.log(f'🚫 Revoked "{name}". The next run will ask again.')
	return 0


VAULT_COMMANDS = [
	Command('vault', 'what credentials this project needs, what is set, what is granted', _status),
	Command(
		'vault-set',
		'store a credential in the OS keychain (prompts; never takes the value as an argument)',
		_set,
		[Param('name', 'credential name', required=True)],
		human_only=True,
	),
	Command(
		'vault-rm',
		'remove a stored credential and its grant',
		_rm,
		[Param('name', 'credential name', required=True)],
		human_only=True,
	),
	Command(
		'vault-grant',
		'allow a credential to be used without asking each time',
		_grant,
		[Param('name', 'credential name', required=True), Param('scenario', 'limit to one scenario id', flag=True)],
		human_only=True,
	),
	Command(
		'vault-revoke',
		'withdraw a credential grant',
		_revoke,
		[Param('name', 'credential name', required=True)],
		human_only=True,
	),
]
