import flet as ft
from services.replication_service import ReplicationService
from domain.replication import ReplicationRule
import uuid

class ReplicatorPage(ft.Container):
    def __init__(self, rep_service: ReplicationService, router):
        super().__init__(expand=True)
        self.rep_service = rep_service
        self.router = router

        self.rules_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("Master")),
                ft.DataColumn(ft.Text("Follower")),
                ft.DataColumn(ft.Text("Multi")),
                ft.DataColumn(ft.Text("Sym Filtro")),
                ft.DataColumn(ft.Text("Estado")),
                ft.DataColumn(ft.Text("Acciones")),
            ],
            rows=[]
        )
        self.rules_container = ft.Container(content=ft.Text("No hay reglas de replicación.", italic=True))
        
        self.logs_listview = ft.ListView(height=200, spacing=4)

        # Form elements for CRUD
        self.txt_master = ft.TextField(label="Master", width=120)
        self.txt_follower = ft.TextField(label="Follower", width=120)
        self.txt_multiplier = ft.TextField(label="Mult (ej 1.5)", width=100, value="1.0")
        self.txt_symbol = ft.TextField(label="Symbol (opc)", width=120)
        
        def save_rule(e):
            if not self.txt_master.value or not self.txt_follower.value:
                return
            try:
                mult = float(self.txt_multiplier.value)
            except:
                mult = 1.0
            
            rule = ReplicationRule(
                id=str(uuid.uuid4()),
                master_account=self.txt_master.value,
                follower_account=self.txt_follower.value,
                multiplier=mult,
                symbol_filter=self.txt_symbol.value if self.txt_symbol.value else None
            )
            self.rep_service.add_rule(rule)
            self.txt_master.value = ""
            self.txt_follower.value = ""
            self.txt_symbol.value = ""
            self.refresh_data()

        add_row = ft.Row([
            self.txt_master,
            self.txt_follower,
            self.txt_multiplier,
            self.txt_symbol,
            ft.IconButton(icon=ft.Icons.ADD_CIRCLE, icon_color=ft.Colors.GREEN, on_click=save_rule, tooltip="Añadir")
        ])

        self.content = ft.Column([
            ft.Text("Panel de Replicador Rápido", size=24, weight="bold"),
            add_row,
            ft.Divider(),
            ft.Text("Reglas Activas:", size=16, weight="bold"),
            self.rules_container,
            
            ft.Container(height=20),
            ft.Text("Auditoría en vivo:", size=16, weight="bold"),
            ft.Container(
                content=self.logs_listview,
                bgcolor=ft.Colors.BLACK38,
                padding=10,
                border_radius=5
            )
        ], expand=True)

    def refresh_data(self):
        rule_rows = []
        for r in self.rep_service.rules:
            rule_id = r.id
            def toggle_rule(e, rid=rule_id):
                self.rep_service.update_rule_status(rid, e.control.value)
                self.refresh_data()
                
            def del_rule(e, rid=rule_id):
                self.rep_service.delete_rule(rid)
                self.refresh_data()

            status_color = ft.Colors.GREEN_400 if r.enabled else ft.Colors.RED_400
            
            actions = ft.Row([
                ft.Switch(value=r.enabled, on_change=toggle_rule),
                ft.IconButton(icon=ft.Icons.DELETE, icon_color=ft.Colors.RED_200, on_click=del_rule, tooltip="Borrar")
            ])
            
            rule_rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(r.master_account, weight="bold")),
                ft.DataCell(ft.Text(r.follower_account)),
                ft.DataCell(ft.Text(f"x{r.multiplier}")),
                ft.DataCell(ft.Text(r.symbol_filter or "TODOS", color=ft.Colors.BLUE_200)),
                ft.DataCell(ft.Text("ON" if r.enabled else "OFF", color=status_color)),
                ft.DataCell(actions),
            ]))

        if rule_rows:
            self.rules_table.rows = rule_rows
            self.rules_container.content = self.rules_table
        else:
            self.rules_container.content = ft.Text("No hay reglas de replicación.", italic=True)

        logs = self.rep_service.audit_service.get_recent_logs(15)
        self.logs_listview.controls = [
            ft.Text(f"[{lg.timestamp.strftime('%H:%M:%S')}] {lg.event_type} - {lg.message}", size=12) 
            for lg in logs
        ]

        if getattr(self, "page", None):
            self.update()

    def did_mount(self):
        self.refresh_data()
