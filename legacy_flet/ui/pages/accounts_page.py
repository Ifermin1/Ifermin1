import flet as ft
from services.account_service import AccountService

class AccountsPage(ft.Container):
    def __init__(self, account_service: AccountService):
        super().__init__(expand=True)
        self.account_service = account_service
        self.bridge = account_service.bridge
        
        # Health Indicators
        self.master_status = ft.Text("Desconocido", color=ft.Colors.GREY)
        self.follower_status = ft.Text("Desconocido", color=ft.Colors.GREY)
        self.sync_status = ft.Text("Desconocido", color=ft.Colors.GREY)
        
        self.last_msg_in_ui = ft.Text("N/A")
        self.last_msg_out_ui = ft.Text("N/A")
        self.error_count_ui = ft.Text("0", color=ft.Colors.GREEN)

        health_card = ft.Card(
            content=ft.Container(
                padding=15,
                content=ft.Column([
                    ft.Text("Métricas de Salud del Bridge (ZMQ)", weight="bold", size=18),
                    ft.Divider(),
                    ft.Row([
                        ft.Column([
                            ft.Text("Estado ZMQ Sockets", weight="bold"),
                            ft.Row([ft.Text("SUB (5555) Master:"), self.master_status]),
                            ft.Row([ft.Text("PUB (5556) Follower:"), self.follower_status]),
                            ft.Row([ft.Text("REQ (5557) Sync:"), self.sync_status]),
                        ], expand=1),
                        ft.Column([
                            ft.Text("Estadísticas", weight="bold"),
                            ft.Row([ft.Text("Último IN:"), self.last_msg_in_ui]),
                            ft.Row([ft.Text("Último OUT:"), self.last_msg_out_ui]),
                            ft.Row([ft.Text("Errores Acumulados:"), self.error_count_ui]),
                        ], expand=1),
                    ])
                ])
            )
        )
        
        self.dt = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("ID de Cuenta")),
                ft.DataColumn(ft.Text("Balance")),
                ft.DataColumn(ft.Text("Estado")),
            ],
            rows=[]
        )
        self.empty_msg = ft.Text("Esperando datos de conexión M5557...", italic=True)
        self.table_container = ft.Container(content=self.empty_msg)

        self.content = ft.Column([
            ft.Text("Live Account View", size=24, weight="bold"),
            health_card,
            ft.Container(height=10),
            ft.Text("Cuentas Sincronizadas", size=18, weight="bold"),
            ft.Divider(),
            self.table_container
        ], expand=True)

    def refresh_data(self):
        accounts = self.account_service.get_all_accounts()
        health = self.bridge.health

        # Update Health
        def set_status(ctrl, is_up):
            ctrl.value = "Conectado" if is_up else "Caído/Timeout"
            ctrl.color = ft.Colors.GREEN_400 if is_up else ft.Colors.RED_400

        set_status(self.master_status, health.status_5555)
        set_status(self.follower_status, health.status_5556)
        set_status(self.sync_status, health.status_5557)

        f_time = lambda d: d.strftime('%H:%M:%S') if d else "N/A"
        self.last_msg_in_ui.value = f_time(health.last_msg_in)
        self.last_msg_out_ui.value = f_time(health.last_msg_out)
        self.error_count_ui.value = str(health.error_count)
        self.error_count_ui.color = ft.Colors.RED if health.error_count > 0 else ft.Colors.GREEN

        if not accounts:
            self.table_container.content = self.empty_msg
        else:
            rows = []
            for acc in accounts:
                rows.append(ft.DataRow(cells=[
                    ft.DataCell(ft.Text(acc.account_id, weight="bold")),
                    ft.DataCell(ft.Text(f"${acc.balance:,.2f}")),
                    ft.DataCell(ft.Text("Active", color=ft.Colors.GREEN_300)),
                ]))
            self.dt.rows = rows
            self.table_container.content = self.dt

        if getattr(self, "page", None):
            self.update()

    def did_mount(self):
        self.refresh_data()
