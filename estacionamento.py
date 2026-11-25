import json
import math
import os
import sqlite3
import threading
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "estacionamento.db")
TOTAL_VAGAS = 20


def conectar_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def inicializar_banco():
    with conectar_db() as conn:
        colunas_antigas = [r[1] for r in conn.execute("PRAGMA table_info(clientes);").fetchall()]
        if "placa" in colunas_antigas:
            conn.execute("DROP TABLE IF EXISTS movimentacoes;")
            conn.execute("DROP TABLE IF EXISTS veiculos;")
            conn.execute("DROP TABLE IF EXISTS clientes;")

        # 1. TABELA DE CLIENTES
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                cpf TEXT UNIQUE NOT NULL,
                telefone TEXT,
                tipo TEXT NOT NULL DEFAULT 'Avulso',
                data_cadastro TEXT NOT NULL
            );
        """)

        # 2. TABELA DE VEÍCULOS
        conn.execute("""
            CREATE TABLE IF NOT EXISTS veiculos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id INTEGER NOT NULL,
                placa TEXT UNIQUE NOT NULL,
                modelo TEXT NOT NULL,
                cor TEXT,
                data_cadastro TEXT NOT NULL,
                FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE
            );
        """)

        # 3. TABELA DE MOVIMENTAÇÕES
        conn.execute("""
            CREATE TABLE IF NOT EXISTS movimentacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id INTEGER,
                veiculo_id INTEGER,
                placa TEXT NOT NULL,
                modelo TEXT,
                vaga INTEGER NOT NULL,
                tipo_cliente TEXT NOT NULL,
                entrada TEXT NOT NULL,
                saida TEXT,
                valor_pago REAL DEFAULT 0.0,
                status TEXT NOT NULL DEFAULT 'Estacionado',
                FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE SET NULL,
                FOREIGN KEY (veiculo_id) REFERENCES veiculos(id) ON DELETE SET NULL
            );
        """)

        # Garante a coluna cliente_id caso a tabela movimentacoes já existisse sem ela
        cols_mov = [r[1] for r in conn.execute("PRAGMA table_info(movimentacoes);").fetchall()]
        if "cliente_id" not in cols_mov:
            conn.execute("ALTER TABLE movimentacoes ADD COLUMN cliente_id INTEGER REFERENCES clientes(id) ON DELETE SET NULL;")

        # Dados iniciais de clientes e veículos se estiverem vazios
        total_clientes = conn.execute("SELECT COUNT(*) FROM clientes;").fetchone()[0]
        if total_clientes == 0:
            agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO clientes (nome, cpf, telefone, tipo, data_cadastro) VALUES (?, ?, ?, ?, ?);",
                ("Carlos Eduardo Mendes", "111.222.333-44", "(41) 99911-2233", "Mensalista", agora),
            )
            carlos_id = cur.lastrowid

            cur.execute(
                "INSERT INTO clientes (nome, cpf, telefone, tipo, data_cadastro) VALUES (?, ?, ?, ?, ?);",
                ("Roberto Almeida", "555.666.777-88", "(41) 98844-5566", "Avulso", agora),
            )
            roberto_id = cur.lastrowid

            cur.executemany(
                "INSERT INTO veiculos (cliente_id, placa, modelo, cor, data_cadastro) VALUES (?, ?, ?, ?, ?);",
                [
                    (carlos_id, "ABC-1D23", "Toyota Corolla", "Prata", agora),
                    (carlos_id, "DEF-4G56", "Honda HR-V", "Branco", agora),
                    (roberto_id, "KLM-4512", "Volkswagen Polo", "Cinza", agora),
                ],
            )

        # Dados iniciais de movimentações caso a tabela esteja vazia
        total_movs = conn.execute("SELECT COUNT(*) FROM movimentacoes;").fetchone()[0]
        if total_movs == 0:
            agora_dt = datetime.now()
            entrada_1 = (agora_dt - timedelta(hours=2, minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
            entrada_2 = (agora_dt - timedelta(minutes=35)).strftime("%Y-%m-%d %H:%M:%S")
            entrada_hist = (agora_dt - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
            saida_hist = (agora_dt - timedelta(hours=3, minutes=10)).strftime("%Y-%m-%d %H:%M:%S")

            veiculos_cadastrados = conn.execute("""
                SELECT v.id AS veiculo_id, v.cliente_id, v.placa, v.modelo, v.cor, c.tipo
                FROM veiculos v
                JOIN clientes c ON v.cliente_id = c.id
                ORDER BY v.id ASC
                LIMIT 2;
            """).fetchall()

            movs_iniciais = []
            if len(veiculos_cadastrados) >= 1:
                v1 = veiculos_cadastrados[0]
                desc1 = f"{v1['modelo']} {v1['cor']}".strip() if v1["cor"] else v1["modelo"]
                movs_iniciais.append((v1["cliente_id"], v1["veiculo_id"], v1["placa"], desc1, 1, v1["tipo"], entrada_1, None, 0.0, "Estacionado"))

            if len(veiculos_cadastrados) >= 2:
                v2 = veiculos_cadastrados[1]
                desc2 = f"{v2['modelo']} {v2['cor']}".strip() if v2["cor"] else v2["modelo"]
                movs_iniciais.append((v2["cliente_id"], v2["veiculo_id"], v2["placa"], desc2, 3, v2["tipo"], entrada_2, None, 0.0, "Estacionado"))

            # Registo finalizado de exemplo para o histórico e caixa
            movs_iniciais.append((None, None, "XYZ-9876", "Hyundai HB20 Vermelho", 4, "Avulso", entrada_hist, saida_hist, 15.0, "Finalizado"))

            conn.executemany(
                """
                INSERT INTO movimentacoes (cliente_id, veiculo_id, placa, modelo, vaga, tipo_cliente, entrada, saida, valor_pago, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                movs_iniciais,
            )

        conn.commit()


def calcular_tarifa(entrada_str, tipo_cliente, referencia_dt=None):
    entrada_dt = datetime.strptime(entrada_str, "%Y-%m-%d %H:%M:%S")
    fim_dt = referencia_dt or datetime.now()
    segundos = max(0, int((fim_dt - entrada_dt).total_seconds()))
    minutos_totais = segundos // 60
    horas_texto = f"{minutos_totais // 60}h {minutos_totais % 60:02d}min"
    horas_cobradas = max(1, math.ceil(segundos / 3600)) if segundos > 0 else 1

    if tipo_cliente == "Mensalista":
        valor = 0.0
    else:
        valor = 10.0 + (horas_cobradas - 1) * 5.0

    return horas_texto, horas_cobradas, valor


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ParkFlow SQLite — Sistema de Estacionamento</title>
  <style>
    :root {
      --bg: #f1f5f9;
      --surface: #ffffff;
      --primary: #4f46e5;
      --primary-hover: #4338ca;
      --text: #0f172a;
      --muted: #64748b;
      --border: #e2e8f0;
      --success: #16a34a;
      --danger: #dc2626;
    }
    * { box-sizing: border-box; font-family: 'Segoe UI', system-ui, sans-serif; }
    body { margin: 0; background: var(--bg); color: var(--text); }
    header {
      background: #ffffff; border-bottom: 1px solid var(--border); padding: 14px 24px;
      display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;
    }
    .brand { display: flex; align-items: center; gap: 12px; }
    .logo {
      width: 38px; height: 38px; border-radius: 10px; background: var(--primary);
      color: #fff; font-weight: 800; display: grid; place-items: center; font-size: 1.1rem;
    }
    .brand h1 { margin: 0; font-size: 1.15rem; display: flex; align-items: center; gap: 8px; }
    .pill-db {
      background: #dcfce7; color: #166534; font-size: 0.75rem;
      padding: 3px 10px; border-radius: 999px; font-weight: 700;
    }
    .brand small { color: var(--muted); display: block; font-size: 0.8rem; }
    .db-path {
      background: #f8fafc; border: 1px solid var(--border); padding: 7px 12px;
      border-radius: 8px; font-size: 0.8rem; color: #334155; font-family: monospace;
    }
    .container { max-width: 1240px; margin: 20px auto; padding: 0 16px; }
    .kpis {
      display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
      gap: 14px; margin-bottom: 20px;
    }
    .kpi-card {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 12px; padding: 16px; box-shadow: 0 1px 2px rgba(0,0,0,.03);
    }
    .kpi-card span { font-size: 0.82rem; color: var(--muted); display: block; }
    .kpi-card strong { font-size: 1.5rem; margin-top: 4px; display: block; }
    .tabs { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
    .tab-btn {
      background: var(--surface); border: 1px solid var(--border); padding: 10px 16px;
      border-radius: 10px; cursor: pointer; font-weight: 600; color: var(--muted);
    }
    .tab-btn.active { background: var(--primary); color: #fff; border-color: var(--primary); }
    .panel { display: none; }
    .panel.active { display: block; }
    .card {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 14px; padding: 20px; margin-bottom: 18px;
    }
    .card-header {
      display: flex; justify-content: space-between; align-items: center;
      flex-wrap: wrap; gap: 10px; margin-bottom: 14px;
    }
    .card h2 { margin: 0; font-size: 1.05rem; }
    .form-grid {
      display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px;
    }
    label { font-size: 0.8rem; font-weight: 600; color: var(--muted); display: block; margin-bottom: 4px; }
    input, select {
      width: 100%; padding: 9px 11px; border: 1px solid var(--border);
      border-radius: 8px; font-size: 0.92rem; background: #fff;
    }
    input:focus, select:focus { outline: 2px solid #c7d2fe; border-color: var(--primary); }
    .btn {
      background: var(--primary); color: #fff; border: none; padding: 10px 16px;
      border-radius: 8px; font-weight: 600; cursor: pointer; font-size: 0.88rem;
    }
    .btn:hover { background: var(--primary-hover); }
    .btn-success { background: var(--success); }
    .btn-danger { background: var(--danger); }
    .btn-outline { background: #f8fafc; color: var(--text); border: 1px solid var(--border); }
    .btn-sm { padding: 6px 10px; font-size: 0.78rem; }
    .vagas-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(105px, 1fr)); gap: 10px;
    }
    .vaga-box {
      border: 2px dashed var(--border); border-radius: 10px; padding: 10px;
      text-align: center; cursor: pointer; transition: 0.15s;
    }
    .vaga-livre { background: #f0fdf4; border-color: #86efac; color: #166534; }
    .vaga-livre:hover { transform: translateY(-2px); border-color: var(--success); }
    .vaga-ocupada { background: #fef2f2; border-style: solid; border-color: #fca5a5; color: #991b1b; }
    .vaga-ocupada:hover { transform: translateY(-2px); border-color: var(--danger); }
    .vaga-box strong { display: block; font-size: 0.95rem; }
    .vaga-box small { font-size: 0.78rem; display: block; margin-top: 3px; font-weight: 700; }
    .table-wrap { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
    th, td { text-align: left; padding: 12px; border-bottom: 1px solid var(--border); }
    th { color: var(--muted); font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.03em; }
    .vaga-pill {
      background: #0f172a; color: #fff; padding: 5px 10px; border-radius: 8px;
      font-weight: 700; font-size: 0.8rem; display: inline-block;
    }
    .placa-pill {
      background: #f1f5f9; border: 1px solid #cbd5e1; color: #0f172a;
      padding: 3px 8px; border-radius: 6px; font-family: monospace; font-weight: 700;
    }
    .badge { padding: 4px 10px; border-radius: 999px; font-size: 0.76rem; font-weight: 700; }
    .badge-mensalista { background: #e0e7ff; color: #3730a3; }
    .badge-avulso { background: #fef3c7; color: #92400e; }
    .actions { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
    .hint {
      background: #f1f5f9; color: #475569; padding: 5px 12px;
      border-radius: 999px; font-size: 0.78rem;
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="logo">P</div>
      <div>
        <h1>ParkFlow SQLite <span class="pill-db">3 Tabelas Ativas</span></h1>
        <small>clientes • veiculos • movimentacoes sincronizados com DB Browser</small>
      </div>
    </div>
    <div class="db-path" id="dbPathBadge">estacionamento.db</div>
  </header>

  <div class="container">
    <div class="kpis">
      <div class="kpi-card"><span>Vagas Livres</span><strong id="kpiLivres" style="color:var(--success)">20 / 20</strong></div>
      <div class="kpi-card"><span>Veículos no Pátio</span><strong id="kpiOcupadas" style="color:var(--danger)">0</strong></div>
      <div class="kpi-card"><span>Clientes Cadastrados</span><strong id="kpiClientes">0</strong></div>
      <div class="kpi-card"><span>Veículos / Placas</span><strong id="kpiVeiculos">0</strong></div>
      <div class="kpi-card"><span>Faturamento Recebido</span><strong id="kpiCaixa" style="color:var(--primary)">R$ 0,00</strong></div>
    </div>

    <div class="tabs">
      <button class="tab-btn active" onclick="abrirAba('patio', this)">1. Controle de Pátio</button>
      <button class="tab-btn" onclick="abrirAba('clientes', this)">2. Clientes</button>
      <button class="tab-btn" onclick="abrirAba('veiculos', this)">3. Veículos e Placas</button>
      <button class="tab-btn" onclick="abrirAba('historico', this)">4. Histórico e Caixa</button>
      <button class="tab-btn" onclick="abrirAba('sql', this)">5. DB Browser / SQLite</button>
    </div>

    <!-- ABA 1: CONTROLE DE PÁTIO -->
    <div id="aba-patio" class="panel active">
      <div class="card">
        <div class="card-header">
          <h2>Registrar Entrada de Veículo</h2>
          <span class="hint">Tarifa: Avulso R$ 10,00 (1ª hora) + R$ 5,00/h • Mensalista: Isento</span>
        </div>
        <form id="formEntrada" onsubmit="registrarEntrada(event)">
          <div class="form-grid">
            <div>
              <label>Veículo / Placa Cadastrada</label>
              <select id="entVeiculo" onchange="preencherDadosEntrada()">
                <option value="">Visitante Avulso (Sem cadastro)</option>
              </select>
            </div>
            <div>
              <label>Placa *</label>
              <input type="text" id="entPlaca" placeholder="ABC-1D23" maxlength="8" required oninput="mascaraPlaca(this)" />
            </div>
            <div>
              <label>Veículo / Cor</label>
              <input type="text" id="entModelo" placeholder="Ex: Renault Kwid Prata" />
            </div>
            <div>
              <label>Categoria</label>
              <select id="entTipo">
                <option value="Avulso">Avulso</option>
                <option value="Mensalista">Mensalista</option>
              </select>
            </div>
            <div>
              <label>Vaga Livre *</label>
              <select id="entVaga" required></select>
            </div>
            <div style="display:flex;align-items:flex-end;">
              <button type="submit" class="btn" style="width:100%">Registrar Entrada</button>
            </div>
          </div>
        </form>
      </div>

      <div class="card">
        <div class="card-header">
          <h2>Mapa de Vagas (20 Vagas)</h2>
          <span class="hint">Clique em uma vaga livre para selecionar ou ocupada para finalizar</span>
        </div>
        <div class="vagas-grid" id="gridVagas"></div>
      </div>

      <div class="card">
        <div class="card-header">
          <h2>Veículos Estacionados no Momento</h2>
          <span class="hint">Use +1h para simular permanência no banco SQLite</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Vaga</th><th>Placa</th><th>Veículo</th><th>Proprietário</th><th>Categoria</th><th>Entrada</th><th>Permanência</th><th>Valor Atual</th><th>Ações</th>
              </tr>
            </thead>
            <tbody id="tabelaPatio"></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ABA 2: CADASTRO DE CLIENTES -->
    <div id="aba-clientes" class="panel">
      <div class="card">
        <div class="card-header">
          <h2 id="tituloFormCliente">Cadastrar Novo Cliente (Tabela: clientes)</h2>
          <span class="hint">Dados pessoais e plano do cliente</span>
        </div>
        <form id="formCliente" onsubmit="salvarCliente(event)">
          <input type="hidden" id="cliId" />
          <div class="form-grid">
            <div>
              <label>Nome Completo *</label>
              <input type="text" id="cliNome" required placeholder="Ex: Yago Johnen Becker" />
            </div>
            <div>
              <label>CPF *</label>
              <input type="text" id="cliCpf" required placeholder="000.000.000-00" maxlength="14" oninput="mascaraCPF(this)" />
            </div>
            <div>
              <label>Telefone</label>
              <input type="text" id="cliTel" placeholder="(41) 99999-9999" maxlength="15" oninput="mascaraTel(this)" />
            </div>
            <div>
              <label>Plano</label>
              <select id="cliTipo">
                <option value="Mensalista">Mensalista</option>
                <option value="Avulso">Avulso</option>
              </select>
            </div>
          </div>
          <div style="margin-top:14px;display:flex;gap:8px;">
            <button type="submit" class="btn btn-success" id="btnSalvarCliente">Salvar Cliente</button>
            <button type="button" class="btn btn-outline" onclick="limparFormCliente()">Cancelar / Limpar</button>
          </div>
        </form>
      </div>

      <div class="card">
        <div class="card-header">
          <h2>Clientes Cadastrados</h2>
          <input type="text" id="buscaCliente" placeholder="Buscar por nome ou CPF..." style="max-width:280px;" oninput="renderizarClientes()" />
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th><th>Nome</th><th>CPF</th><th>Telefone</th><th>Plano</th><th>Qtd. Veículos</th><th>Ações</th>
              </tr>
            </thead>
            <tbody id="tabelaClientes"></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ABA 3: CADASTRO DE VEÍCULOS E PLACAS -->
    <div id="aba-veiculos" class="panel">
      <div class="card">
        <div class="card-header">
          <h2 id="tituloFormVeiculo">Cadastrar Veículo / Placa (Tabela: veiculos)</h2>
          <span class="hint">Vincule uma ou mais placas a um cliente cadastrado</span>
        </div>
        <form id="formVeiculo" onsubmit="salvarVeiculo(event)">
          <input type="hidden" id="veiId" />
          <div class="form-grid">
            <div>
              <label>Proprietário (Cliente) *</label>
              <select id="veiClienteId" required></select>
            </div>
            <div>
              <label>Placa *</label>
              <input type="text" id="veiPlaca" required placeholder="JFU-7989" maxlength="8" oninput="mascaraPlaca(this)" />
            </div>
            <div>
              <label>Modelo *</label>
              <input type="text" id="veiModelo" required placeholder="Ex: Renault Kwid" />
            </div>
            <div>
              <label>Cor</label>
              <input type="text" id="veiCor" placeholder="Ex: Prata" />
            </div>
          </div>
          <div style="margin-top:14px;display:flex;gap:8px;">
            <button type="submit" class="btn btn-success" id="btnSalvarVeiculo">Salvar Veículo</button>
            <button type="button" class="btn btn-outline" onclick="limparFormVeiculo()">Cancelar / Limpar</button>
          </div>
        </form>
      </div>

      <div class="card">
        <div class="card-header">
          <h2>Veículos e Placas Registrados</h2>
          <input type="text" id="buscaVeiculo" placeholder="Buscar por placa, modelo ou cliente..." style="max-width:290px;" oninput="renderizarVeiculos()" />
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th><th>Placa</th><th>Modelo</th><th>Cor</th><th>Proprietário (Cliente)</th><th>Plano</th><th>Ações</th>
              </tr>
            </thead>
            <tbody id="tabelaVeiculos"></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ABA 4: HISTÓRICO -->
    <div id="aba-historico" class="panel">
      <div class="card">
        <div class="card-header">
          <h2>Histórico de Movimentações Finalizadas</h2>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th><th>Placa</th><th>Vaga</th><th>Categoria</th><th>Entrada</th><th>Saída</th><th>Valor Pago</th>
              </tr>
            </thead>
            <tbody id="tabelaHistorico"></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ABA 5: SQLITE / DB BROWSER -->
    <div id="aba-sql" class="panel">
      <div class="card">
        <div class="card-header">
          <h2>Consulta Direta ao Banco SQLite</h2>
        </div>
        <p style="color:var(--muted);font-size:0.88rem;margin-top:0;">
          Arquivo sincronizado para abrir no <strong>DB Browser for SQLite</strong>:
          <code id="caminhoCompletoDb" style="color:var(--text);font-weight:700;"></code>
        </p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;">
          <button class="btn btn-sm" onclick="rodarPresetSQL('SELECT * FROM movimentacoes ORDER BY id DESC;')">SELECT * FROM movimentacoes</button>
          <button class="btn btn-sm" onclick="rodarPresetSQL('SELECT * FROM clientes ORDER BY id DESC;')">SELECT * FROM clientes</button>
          <button class="btn btn-sm" onclick="rodarPresetSQL('SELECT * FROM veiculos ORDER BY id DESC;')">SELECT * FROM veiculos</button>
          <button class="btn btn-sm btn-outline" onclick="rodarPresetSQL('SELECT m.id, m.vaga, m.placa, m.modelo, c.nome AS cliente, m.status, m.valor_pago FROM movimentacoes m LEFT JOIN clientes c ON m.cliente_id = c.id;')">JOIN Movimentações + Clientes</button>
        </div>
        <div style="display:flex;gap:8px;margin-bottom:14px;">
          <input type="text" id="inputSql" value="SELECT * FROM movimentacoes ORDER BY id DESC;" />
          <button class="btn" onclick="executarSQL()">Executar SQL</button>
        </div>
        <div class="table-wrap">
          <table id="tabelaSql"></table>
        </div>
      </div>
    </div>
  </div>

  <script>
    let estado = { clientes: [], veiculos: [], estacionados: [], historico: [], kpis: {}, db_path: '' };

    function abrirAba(nome, btn) {
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.getElementById('aba-' + nome).classList.add('active');
      btn.classList.add('active');
      if (nome === 'sql') executarSQL();
    }

    function mascaraCPF(el) {
      let v = el.value.replace(/\D/g, '').slice(0, 11);
      v = v.replace(/(\d{3})(\d)/, '$1.$2');
      v = v.replace(/(\d{3})(\d)/, '$1.$2');
      v = v.replace(/(\d{3})(\d{1,2})$/, '$1-$2');
      el.value = v;
    }

    function mascaraTel(el) {
      let v = el.value.replace(/\D/g, '').slice(0, 11);
      v = v.replace(/^(\d{2})(\d)/g, '($1) $2');
      v = v.replace(/(\d)(\d{4})$/, '$1-$2');
      el.value = v;
    }

    function mascaraPlaca(el) {
      let v = el.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 7);
      if (v.length > 3) v = v.slice(0, 3) + '-' + v.slice(3);
      el.value = v;
    }

    async function carregarTudo() {
      const resp = await fetch('/api/dados');
      estado = await resp.json();

      document.getElementById('dbPathBadge').textContent = estado.db_path;
      document.getElementById('caminhoCompletoDb').textContent = estado.db_path;
      document.getElementById('kpiLivres').textContent = `${estado.kpis.livres} / ${estado.kpis.total_vagas}`;
      document.getElementById('kpiOcupadas').textContent = estado.kpis.ocupadas;
      document.getElementById('kpiClientes').textContent = estado.kpis.total_clientes;
      document.getElementById('kpiVeiculos').textContent = estado.kpis.total_veiculos;
      document.getElementById('kpiCaixa').textContent = 'R$ ' + Number(estado.kpis.faturamento).toFixed(2).replace('.', ',');

      atualizarSelects();
      renderizarVagas();
      renderizarPatio();
      renderizarClientes();
      renderizarVeiculos();
      renderizarHistorico();
    }

    function atualizarSelects() {
      const selVei = document.getElementById('entVeiculo');
      const valVei = selVei.value;
      selVei.innerHTML = '<option value="">Visitante Avulso (Sem cadastro)</option>';
      estado.veiculos.forEach(v => {
        const opt = document.createElement('option');
        opt.value = v.id;
        opt.textContent = `${v.placa} — ${v.modelo} (${v.cliente_nome} • ${v.cliente_tipo})`;
        selVei.appendChild(opt);
      });
      selVei.value = valVei;

      const selCli = document.getElementById('veiClienteId');
      const valCli = selCli.value;
      selCli.innerHTML = '<option value="">Selecione um cliente...</option>';
      estado.clientes.forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.id;
        opt.textContent = `${c.nome} (${c.cpf} • ${c.tipo})`;
        selCli.appendChild(opt);
      });
      if (valCli) selCli.value = valCli;
    }

    function preencherDadosEntrada() {
      const id = document.getElementById('entVeiculo').value;
      const vei = estado.veiculos.find(v => String(v.id) === String(id));
      if (vei) {
        document.getElementById('entPlaca').value = vei.placa;
        document.getElementById('entModelo').value = vei.cor ? `${vei.modelo} ${vei.cor}` : vei.modelo;
        document.getElementById('entTipo').value = vei.cliente_tipo;
      } else {
        document.getElementById('entPlaca').value = '';
        document.getElementById('entModelo').value = '';
        document.getElementById('entTipo').value = 'Avulso';
      }
    }

    function renderizarVagas() {
      const grid = document.getElementById('gridVagas');
      const selVaga = document.getElementById('entVaga');
      grid.innerHTML = '';
      selVaga.innerHTML = '';

      const mapaOcupadas = {};
      estado.estacionados.forEach(m => { mapaOcupadas[m.vaga] = m; });

      for (let v = 1; v <= estado.kpis.total_vagas; v++) {
        const mov = mapaOcupadas[v];
        const div = document.createElement('div');
        if (mov) {
          div.className = 'vaga-box vaga-ocupada';
          div.innerHTML = `<strong>Vaga ${String(v).padStart(2, '0')}</strong><small>${mov.placa}</small>`;
          div.onclick = () => registrarSaida(mov.id, mov.placa, mov.tipo_cliente, mov.entrada);
        } else {
          div.className = 'vaga-box vaga-livre';
          div.innerHTML = `<strong>Vaga ${String(v).padStart(2, '0')}</strong><small>Livre</small>`;
          div.onclick = () => { selVaga.value = v; document.getElementById('entPlaca').focus(); };

          const opt = document.createElement('option');
          opt.value = v;
          opt.textContent = `Vaga ${String(v).padStart(2, '0')}`;
          selVaga.appendChild(opt);
        }
        grid.appendChild(div);
      }
    }

    function renderizarPatio() {
      const tbody = document.getElementById('tabelaPatio');
      tbody.innerHTML = '';
      if (estado.estacionados.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--muted);">Nenhum veículo estacionado no momento.</td></tr>';
        return;
      }
      estado.estacionados.forEach(m => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td><span class="vaga-pill">Vaga ${String(m.vaga).padStart(2, '0')}</span></td>
          <td><span class="placa-pill">${m.placa}</span></td>
          <td>${m.modelo || '-'}</td>
          <td>${m.cliente_nome}</td>
          <td><span class="badge ${m.tipo_cliente === 'Mensalista' ? 'badge-mensalista' : 'badge-avulso'}">${m.tipo_cliente}</span></td>
          <td>${m.entrada}</td>
          <td>${m.permanencia}</td>
          <td><strong style="color:var(--primary)">R$ ${Number(m.valor_atual).toFixed(2).replace('.', ',')}</strong></td>
          <td class="actions">
            <button class="btn btn-outline btn-sm" onclick="simularHora(${m.id})">+1h</button>
            <button class="btn btn-danger btn-sm" onclick="registrarSaida(${m.id}, '${m.placa}', '${m.tipo_cliente}', '${m.entrada}')">Registrar Saída</button>
          </td>
        `;
        tbody.appendChild(tr);
      });
    }

    function renderizarClientes() {
      const termo = document.getElementById('buscaCliente').value.toLowerCase();
      const tbody = document.getElementById('tabelaClientes');
      tbody.innerHTML = '';

      const filtrados = estado.clientes.filter(c =>
        c.nome.toLowerCase().includes(termo) || c.cpf.toLowerCase().includes(termo)
      );

      filtrados.forEach(c => {
        const qtdVeiculos = estado.veiculos.filter(v => v.cliente_id === c.id).length;
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${c.id}</td>
          <td><strong>${c.nome}</strong></td>
          <td>${c.cpf}</td>
          <td>${c.telefone || '-'}</td>
          <td><span class="badge ${c.tipo === 'Mensalista' ? 'badge-mensalista' : 'badge-avulso'}">${c.tipo}</span></td>
          <td><strong>${qtdVeiculos}</strong> veículo(s)</td>
          <td class="actions">
            <button class="btn btn-sm" onclick="atalhoNovoVeiculo(${c.id})">+ Veículo</button>
            <button class="btn btn-outline btn-sm" onclick="editarCliente(${c.id})">Editar</button>
            <button class="btn btn-danger btn-sm" onclick="excluirCliente(${c.id})">Excluir</button>
          </td>
        `;
        tbody.appendChild(tr);
      });
    }

    function renderizarVeiculos() {
      const termo = document.getElementById('buscaVeiculo').value.toLowerCase();
      const tbody = document.getElementById('tabelaVeiculos');
      tbody.innerHTML = '';

      const filtrados = estado.veiculos.filter(v =>
        v.placa.toLowerCase().includes(termo) ||
        v.modelo.toLowerCase().includes(termo) ||
        v.cliente_nome.toLowerCase().includes(termo)
      );

      filtrados.forEach(v => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${v.id}</td>
          <td><span class="placa-pill">${v.placa}</span></td>
          <td><strong>${v.modelo}</strong></td>
          <td>${v.cor || '-'}</td>
          <td>${v.cliente_nome}</td>
          <td><span class="badge ${v.cliente_tipo === 'Mensalista' ? 'badge-mensalista' : 'badge-avulso'}">${v.cliente_tipo}</span></td>
          <td class="actions">
            <button class="btn btn-sm" onclick="atalhoEntradaVeiculo(${v.id})">Dar Entrada</button>
            <button class="btn btn-outline btn-sm" onclick="editarVeiculo(${v.id})">Editar</button>
            <button class="btn btn-danger btn-sm" onclick="excluirVeiculo(${v.id})">Excluir</button>
          </td>
        `;
        tbody.appendChild(tr);
      });
    }

    function renderizarHistorico() {
      const tbody = document.getElementById('tabelaHistorico');
      tbody.innerHTML = '';
      estado.historico.forEach(h => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${h.id}</td>
          <td><span class="placa-pill">${h.placa}</span></td>
          <td>Vaga ${String(h.vaga).padStart(2, '0')}</td>
          <td>${h.tipo_cliente}</td>
          <td>${h.entrada}</td>
          <td>${h.saida}</td>
          <td><strong>R$ ${Number(h.valor_pago).toFixed(2).replace('.', ',')}</strong></td>
        `;
        tbody.appendChild(tr);
      });
    }

    async function salvarCliente(e) {
      e.preventDefault();
      const payload = {
        id: document.getElementById('cliId').value || null,
        nome: document.getElementById('cliNome').value.trim(),
        cpf: document.getElementById('cliCpf').value.trim(),
        telefone: document.getElementById('cliTel').value.trim(),
        tipo: document.getElementById('cliTipo').value,
      };
      const resp = await fetch('/api/clientes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const res = await resp.json();
      if (!resp.ok) { alert(res.erro || 'Erro ao salvar cliente.'); return; }
      limparFormCliente();
      await carregarTudo();
    }

    function editarCliente(id) {
      const c = estado.clientes.find(item => item.id === id);
      if (!c) return;
      document.getElementById('cliId').value = c.id;
      document.getElementById('cliNome').value = c.nome;
      document.getElementById('cliCpf').value = c.cpf;
      document.getElementById('cliTel').value = c.telefone || '';
      document.getElementById('cliTipo').value = c.tipo;
      document.getElementById('tituloFormCliente').textContent = 'Editando Cliente #' + c.id;
      document.getElementById('btnSalvarCliente').textContent = 'Atualizar Cliente';
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    function limparFormCliente() {
      document.getElementById('formCliente').reset();
      document.getElementById('cliId').value = '';
      document.getElementById('tituloFormCliente').textContent = 'Cadastrar Novo Cliente (Tabela: clientes)';
      document.getElementById('btnSalvarCliente').textContent = 'Salvar Cliente';
    }

    async function excluirCliente(id) {
      if (!confirm('Excluir este cliente também removerá os veículos vinculados a ele. Continuar?')) return;
      await fetch('/api/clientes?id=' + id, { method: 'DELETE' });
      await carregarTudo();
    }

    function atalhoNovoVeiculo(clienteId) {
      document.querySelectorAll('.tab-btn')[2].click();
      document.getElementById('veiClienteId').value = clienteId;
      document.getElementById('veiPlaca').focus();
    }

    async function salvarVeiculo(e) {
      e.preventDefault();
      const payload = {
        id: document.getElementById('veiId').value || null,
        cliente_id: document.getElementById('veiClienteId').value,
        placa: document.getElementById('veiPlaca').value.trim().toUpperCase(),
        modelo: document.getElementById('veiModelo').value.trim(),
        cor: document.getElementById('veiCor').value.trim(),
      };
      const resp = await fetch('/api/veiculos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const res = await resp.json();
      if (!resp.ok) { alert(res.erro || 'Erro ao salvar veículo.'); return; }
      limparFormVeiculo();
      await carregarTudo();
    }

    function editarVeiculo(id) {
      const v = estado.veiculos.find(item => item.id === id);
      if (!v) return;
      document.getElementById('veiId').value = v.id;
      document.getElementById('veiClienteId').value = v.cliente_id;
      document.getElementById('veiPlaca').value = v.placa;
      document.getElementById('veiModelo').value = v.modelo;
      document.getElementById('veiCor').value = v.cor || '';
      document.getElementById('tituloFormVeiculo').textContent = 'Editando Veículo #' + v.id;
      document.getElementById('btnSalvarVeiculo').textContent = 'Atualizar Veículo';
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    function limparFormVeiculo() {
      document.getElementById('formVeiculo').reset();
      document.getElementById('veiId').value = '';
      document.getElementById('tituloFormVeiculo').textContent = 'Cadastrar Veículo / Placa (Tabela: veiculos)';
      document.getElementById('btnSalvarVeiculo').textContent = 'Salvar Veículo';
    }

    async function excluirVeiculo(id) {
      if (!confirm('Deseja excluir este veículo do banco de dados?')) return;
      await fetch('/api/veiculos?id=' + id, { method: 'DELETE' });
      await carregarTudo();
    }

    function atalhoEntradaVeiculo(veiculoId) {
      document.querySelectorAll('.tab-btn')[0].click();
      document.getElementById('entVeiculo').value = veiculoId;
      preencherDadosEntrada();
    }

    async function registrarEntrada(e) {
      e.preventDefault();
      const payload = {
        veiculo_id: document.getElementById('entVeiculo').value || null,
        placa: document.getElementById('entPlaca').value.trim().toUpperCase(),
        modelo: document.getElementById('entModelo').value.trim(),
        tipo_cliente: document.getElementById('entTipo').value,
        vaga: Number(document.getElementById('entVaga').value),
      };
      const resp = await fetch('/api/entrada', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const res = await resp.json();
      if (!resp.ok) { alert(res.erro || 'Erro ao registrar entrada.'); return; }
      document.getElementById('formEntrada').reset();
      await carregarTudo();
    }

    async function simularHora(id) {
      await fetch('/api/simular_hora', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
      });
      await carregarTudo();
    }

    async function registrarSaida(id, placa, tipo, entrada) {
      if (!confirm(`Confirmar saída do veículo ${placa} (${tipo})?\nEntrada: ${entrada}`)) return;
      const resp = await fetch('/api/saida', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
      });
      const res = await resp.json();
      if (!resp.ok) { alert(res.erro || 'Erro ao finalizar saída.'); return; }
      alert(`Saída registrada!\nTempo cobrado: ${res.horas}h\nTotal pago: R$ ${Number(res.valor_pago).toFixed(2).replace('.', ',')}`);
      await carregarTudo();
    }

    function rodarPresetSQL(query) {
      document.getElementById('inputSql').value = query;
      executarSQL();
    }

    async function executarSQL() {
      const query = document.getElementById('inputSql').value.trim();
      if (!query) return;
      const resp = await fetch('/api/sql', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      });
      const res = await resp.json();
      const table = document.getElementById('tabelaSql');
      if (!resp.ok) {
        table.innerHTML = `<tr><td style="color:var(--danger)"><strong>Erro SQL:</strong> ${res.erro}</td></tr>`;
        return;
      }
      if (!res.colunas || res.colunas.length === 0) {
        table.innerHTML = '<tr><td style="color:var(--success)">Comando executado e salvo no SQLite!</td></tr>';
        await carregarTudo();
        return;
      }
      let html = '<thead><tr>' + res.colunas.map(c => `<th>${c}</th>`).join('') + '</tr></thead><tbody>';
      res.linhas.forEach(linha => {
        html += '<tr>' + linha.map(val => `<td>${val === null ? 'NULL' : val}</td>`).join('') + '</tr>';
      });
      html += '</tbody>';
      table.innerHTML = html;
    }

    carregarTudo();
    setInterval(carregarTudo, 30000);
  </script>
</body>
</html>
"""


class EstacionamentoHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def enviar_json(self, dados, status=200):
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def ler_json(self):
        tamanho = int(self.headers.get("Content-Length", 0))
        if tamanho == 0:
            return {}
        return json.loads(self.rfile.read(tamanho).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            pagina = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(pagina)))
            self.end_headers()
            self.wfile.write(pagina)
            return

        if parsed.path == "/api/dados":
            with conectar_db() as conn:
                clientes = [dict(r) for r in conn.execute("SELECT * FROM clientes ORDER BY id DESC;").fetchall()]
                veiculos = [
                    dict(r)
                    for r in conn.execute("""
                        SELECT v.*, c.nome AS cliente_nome, c.tipo AS cliente_tipo
                        FROM veiculos v
                        JOIN clientes c ON v.cliente_id = c.id
                        ORDER BY v.id DESC;
                    """).fetchall()
                ]
                rows_patio = conn.execute("""
                    SELECT m.*, COALESCE(c.nome, 'Visitante Avulso') AS cliente_nome
                    FROM movimentacoes m
                    LEFT JOIN veiculos v ON m.veiculo_id = v.id
                    LEFT JOIN clientes c ON COALESCE(m.cliente_id, v.cliente_id) = c.id
                    WHERE m.status = 'Estacionado'
                    ORDER BY m.vaga ASC;
                """).fetchall()

                estacionados = []
                for r in rows_patio:
                    item = dict(r)
                    permanencia, _, valor_atual = calcular_tarifa(item["entrada"], item["tipo_cliente"])
                    item["permanencia"] = permanencia
                    item["valor_atual"] = valor_atual
                    estacionados.append(item)

                historico = [
                    dict(r)
                    for r in conn.execute("""
                        SELECT * FROM movimentacoes
                        WHERE status = 'Finalizado'
                        ORDER BY id DESC LIMIT 100;
                    """).fetchall()
                ]
                faturamento = conn.execute(
                    "SELECT COALESCE(SUM(valor_pago), 0) FROM movimentacoes WHERE status = 'Finalizado';"
                ).fetchone()[0]

            self.enviar_json({
                "db_path": DB_PATH,
                "clientes": clientes,
                "veiculos": veiculos,
                "estacionados": estacionados,
                "historico": historico,
                "kpis": {
                    "total_vagas": TOTAL_VAGAS,
                    "ocupadas": len(estacionados),
                    "livres": TOTAL_VAGAS - len(estacionados),
                    "total_clientes": len(clientes),
                    "total_veiculos": len(veiculos),
                    "faturamento": faturamento,
                },
            })
            return

        self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        dados = self.ler_json()

        if parsed.path == "/api/clientes":
            nome = (dados.get("nome") or "").strip()
            cpf = (dados.get("cpf") or "").strip()
            telefone = (dados.get("telefone") or "").strip()
            tipo = dados.get("tipo") or "Avulso"
            cid = dados.get("id")

            if not nome or not cpf:
                self.enviar_json({"erro": "Nome e CPF são obrigatórios."}, 400)
                return

            try:
                with conectar_db() as conn:
                    if cid:
                        conn.execute(
                            "UPDATE clientes SET nome=?, cpf=?, telefone=?, tipo=? WHERE id=?;",
                            (nome, cpf, telefone, tipo, int(cid)),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO clientes (nome, cpf, telefone, tipo, data_cadastro) VALUES (?, ?, ?, ?, ?);",
                            (nome, cpf, telefone, tipo, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                        )
                    conn.commit()
                self.enviar_json({"ok": True})
            except sqlite3.IntegrityError:
                self.enviar_json({"erro": "Já existe um cliente cadastrado com este CPF."}, 400)
            return

        if parsed.path == "/api/veiculos":
            cliente_id = dados.get("cliente_id")
            placa = (dados.get("placa") or "").strip().upper()
            modelo = (dados.get("modelo") or "").strip()
            cor = (dados.get("cor") or "").strip()
            vid = dados.get("id")

            if not cliente_id or not placa or not modelo:
                self.enviar_json({"erro": "Proprietário, Placa e Modelo são obrigatórios."}, 400)
                return

            try:
                with conectar_db() as conn:
                    if vid:
                        conn.execute(
                            "UPDATE veiculos SET cliente_id=?, placa=?, modelo=?, cor=? WHERE id=?;",
                            (int(cliente_id), placa, modelo, cor, int(vid)),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO veiculos (cliente_id, placa, modelo, cor, data_cadastro) VALUES (?, ?, ?, ?, ?);",
                            (int(cliente_id), placa, modelo, cor, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                        )
                    conn.commit()
                self.enviar_json({"ok": True})
            except sqlite3.IntegrityError:
                self.enviar_json({"erro": "Esta placa já está cadastrada em outro veículo."}, 400)
            return

        if parsed.path == "/api/entrada":
            placa = (dados.get("placa") or "").strip().upper()
            modelo = (dados.get("modelo") or "").strip() or "Não informado"
            tipo_cliente = dados.get("tipo_cliente") or "Avulso"
            vaga = int(dados.get("vaga") or 0)
            veiculo_id = int(dados["veiculo_id"]) if dados.get("veiculo_id") else None

            if not placa or vaga < 1 or vaga > TOTAL_VAGAS:
                self.enviar_json({"erro": "Placa ou vaga inválida."}, 400)
                return

            with conectar_db() as conn:
                # Verifica se já está estacionado
                ja_estacionado = conn.execute(
                    "SELECT id FROM movimentacoes WHERE (placa = ? OR vaga = ?) AND status = 'Estacionado';",
                    (placa, vaga),
                ).fetchone()
                if ja_estacionado:
                    self.enviar_json({"erro": "Esta placa já está no pátio ou a vaga foi ocupada."}, 400)
                    return

                cliente_id = None

                if veiculo_id:
                    # Veículo já cadastrado selecionado no formulário
                    row_v = conn.execute("SELECT cliente_id FROM veiculos WHERE id = ?;", (veiculo_id,)).fetchone()
                    if row_v:
                        cliente_id = row_v["cliente_id"]
                else:
                    # Placa digitada manualmente → tenta achar ou cadastra automaticamente
                    row_existente = conn.execute(
                        "SELECT id, cliente_id FROM veiculos WHERE placa = ?;", (placa,)
                    ).fetchone()

                    if row_existente:
                        # Placa já existe → só vincula
                        veiculo_id = row_existente["id"]
                        cliente_id = row_existente["cliente_id"]
                    else:
                        # Placa nova → cria cliente + veículo automaticamente
                        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        cpf_temporario = f"AVULSO-{placa}"

                        # Cria cliente
                        cur = conn.execute(
                            """
                            INSERT INTO clientes (nome, cpf, telefone, tipo, data_cadastro)
                            VALUES (?, ?, ?, ?, ?);
                            """,
                            (f"Visitante ({placa})", cpf_temporario, None, tipo_cliente, agora),
                        )
                        cliente_id = cur.lastrowid

                        # Cria veículo
                        cur = conn.execute(
                            """
                            INSERT INTO veiculos (cliente_id, placa, modelo, cor, data_cadastro)
                            VALUES (?, ?, ?, ?, ?);
                            """,
                            (cliente_id, placa, modelo, None, agora),
                        )
                        veiculo_id = cur.lastrowid

                # Registra a movimentação
                conn.execute(
                    """
                    INSERT INTO movimentacoes (cliente_id, veiculo_id, placa, modelo, vaga, tipo_cliente, entrada, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'Estacionado');
                    """,
                    (cliente_id, veiculo_id, placa, modelo, vaga, tipo_cliente, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                )
                conn.commit()

            self.enviar_json({"ok": True})
            return

        if parsed.path == "/api/simular_hora":
            mov_id = int(dados.get("id") or 0)
            with conectar_db() as conn:
                mov = conn.execute("SELECT entrada FROM movimentacoes WHERE id = ?;", (mov_id,)).fetchone()
                if mov:
                    nova_entrada = datetime.strptime(mov["entrada"], "%Y-%m-%d %H:%M:%S") - timedelta(hours=1)
                    conn.execute(
                        "UPDATE movimentacoes SET entrada = ? WHERE id = ?;",
                        (nova_entrada.strftime("%Y-%m-%d %H:%M:%S"), mov_id),
                    )
                    conn.commit()
            self.enviar_json({"ok": True})
            return

        if parsed.path == "/api/saida":
            mov_id = int(dados.get("id") or 0)
            with conectar_db() as conn:
                mov = conn.execute("SELECT * FROM movimentacoes WHERE id = ?;", (mov_id,)).fetchone()
                if not mov:
                    self.enviar_json({"erro": "Registro não encontrado."}, 404)
                    return

                saida_dt = datetime.now()
                _, horas, valor_pago = calcular_tarifa(mov["entrada"], mov["tipo_cliente"], saida_dt)

                conn.execute(
                    """
                    UPDATE movimentacoes
                    SET saida = ?, valor_pago = ?, status = 'Finalizado'
                    WHERE id = ?;
                    """,
                    (saida_dt.strftime("%Y-%m-%d %H:%M:%S"), valor_pago, mov_id),
                )
                conn.commit()
            self.enviar_json({"ok": True, "horas": horas, "valor_pago": valor_pago})
            return

        if parsed.path == "/api/sql":
            query = (dados.get("query") or "").strip()
            try:
                with conectar_db() as conn:
                    cur = conn.execute(query)
                    if cur.description:
                        colunas = [d[0] for d in cur.description]
                        linhas = [list(r) for r in cur.fetchall()]
                        self.enviar_json({"colunas": colunas, "linhas": linhas})
                    else:
                        conn.commit()
                        self.enviar_json({"colunas": [], "linhas": []})
            except sqlite3.Error as e:
                self.enviar_json({"erro": str(e)}, 400)
            return

        self.send_error(404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        item_id = int(qs.get("id", [0])[0])

        if parsed.path == "/api/clientes":
            with conectar_db() as conn:
                conn.execute("DELETE FROM clientes WHERE id = ?;", (item_id,))
                conn.commit()
            self.enviar_json({"ok": True})
            return

        if parsed.path == "/api/veiculos":
            with conectar_db() as conn:
                conn.execute("DELETE FROM veiculos WHERE id = ?;", (item_id,))
                conn.commit()
            self.enviar_json({"ok": True})
            return

        self.send_error(404)


if __name__ == "__main__":
    inicializar_banco()

    servidor = None
    porta_escolhida = 8080
    for porta in range(8080, 8095):
        try:
            servidor = ThreadingHTTPServer(("127.0.0.1", porta), EstacionamentoHandler)
            porta_escolhida = porta
            break
        except OSError:
            continue

    url = f"http://127.0.0.1:{porta_escolhida}"
    print(f"Sistema iniciado em: {url}")
    print(f"Arquivo SQLite criado em: {DB_PATH}")
    print("Pressione Ctrl+C para encerrar.")

    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
        servidor.server_close()
