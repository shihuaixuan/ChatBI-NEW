import { request } from '@/utils/request'
export const AuthApi = {
  login: (credentials: { username: string; password: string }) => {
    return request.post<{
      data: any
      token: string
    }>('/login/access-token', credentials, {
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
    })
  },
  logout: (data: any) => request.post('/login/logout', data),
  info: () => request.get('/user/info'),
}
