import { useState, type FormEvent } from 'react';
import { LockKeyhole } from 'lucide-react';
import { Button, InlineAlert, Input } from '../components/common';
import { useAuth } from '../contexts/AuthContext';

export default function LoginPage() {
  const { login, passwordSet } = useAuth();
  const [password, setPassword] = useState('');
  const [passwordConfirm, setPasswordConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    if (!password.trim()) {
      setError('请输入管理员密码。');
      return;
    }
    if (!passwordSet && password !== passwordConfirm) {
      setError('两次输入的密码不一致。');
      return;
    }

    setSubmitting(true);
    const result = await login(password, passwordSet ? undefined : passwordConfirm);
    if (!result.success) {
      setError(result.error?.message || '登录失败，请稍后重试。');
    }
    setSubmitting(false);
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-base px-4 py-10">
      <section className="terminal-card w-full max-w-md rounded-2xl p-6 shadow-soft-card sm:p-8" data-testid="login-page">
        <div className="mb-6 flex items-center gap-3">
          <span className="flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <LockKeyhole className="size-5" />
          </span>
          <div>
            <h1 className="text-xl font-semibold text-foreground">登录 Stock Assistant</h1>
            <p className="mt-1 text-sm text-secondary-text">
              {passwordSet ? '请输入管理员密码继续。' : '首次登录请设置管理员密码。'}
            </p>
          </div>
        </div>

        {error ? <InlineAlert variant="danger" title="登录失败" message={error} className="mb-5" /> : null}

        <form className="space-y-4" onSubmit={(event) => void handleSubmit(event)}>
          <Input
            label={passwordSet ? '管理员密码' : '设置管理员密码'}
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete={passwordSet ? 'current-password' : 'new-password'}
            iconType="password"
            allowTogglePassword
            autoFocus
          />
          {!passwordSet ? (
            <Input
              label="确认密码"
              type="password"
              value={passwordConfirm}
              onChange={(event) => setPasswordConfirm(event.target.value)}
              autoComplete="new-password"
              iconType="password"
              allowTogglePassword
            />
          ) : null}
          <Button type="submit" className="w-full" isLoading={submitting} loadingText="登录中...">
            登录
          </Button>
        </form>
      </section>
    </main>
  );
}
