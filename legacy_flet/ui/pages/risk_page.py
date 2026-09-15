import flet as ft

class RiskPage(ft.Container):
    def __init__(self):
        super().__init__(expand=True)
        self.content = ft.Column([
            ft.Text("Módulo de Riesgo (Fase 2)", size=24, weight="bold"),
            ft.ElevatedButton("KILL SWITCH GLOBAL", icon=ft.Icons.DANGEROUS, color=ft.Colors.WHITE, bgcolor=ft.Colors.RED_700),
            ft.Text("Próximamente...", italic=True)
        ])
