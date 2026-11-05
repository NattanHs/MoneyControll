"""Contas de acesso (usuário + senha embaralhada). NÃO edite à mão: use ferramentas/cadastrar-cliente.py.

Este arquivo é publicado junto com o app (repositório público), por isso guarda apenas o hash da senha,
nunca a senha. Depois de cadastrar/remover um cliente, faça commit e republique.
"""
CONTAS = {
    'gustavo.lacerda': {"nome": 'Gustavo Lacerda', "salt": '25afa6414c7c1c8a8629f2ce300eea0d', "hash": '07e2e45c6186928694888dc9538b270e548eb9a40f55fa1bcd93b997d26e46ef', "iters": 200000, "ate": '2027-10-29'},
    'jhon': {"nome": 'Jhon', "salt": 'b17bf4d9a4844fdd7f49ab7068795487', "hash": 'e3b66902b25c963dd84eb9aa50d65335fcc276f427a7224509875c919b9905ae', "iters": 200000, "ate": '2027-10-29'},
}
